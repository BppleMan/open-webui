"""Shared streaming/replay tag projection. The parser mutates output items in place."""

import re
import time
from uuid import uuid4

DEFAULT_REASONING_TAGS = [
    ('<think>', '</think>'),
    ('<thinking>', '</thinking>'),
    ('<reason>', '</reason>'),
    ('<reasoning>', '</reasoning>'),
    ('<thought>', '</thought>'),
    ('<Thought>', '</Thought>'),
    ('<|begin_of_thought|>', '<|end_of_thought|>'),
    ('◁think▷', '◁/think▷'),
]

DEFAULT_SOLUTION_TAGS = [('<|begin_of_solution|>', '<|end_of_solution|>')]


def output_id(prefix):
    return f'{prefix}_{uuid4().hex[:24]}'


def _start_tag_pattern(start_tag):
    if start_tag.startswith('<') and start_tag.endswith('>'):
        return rf'<{re.escape(start_tag[1:-1])}(\s.*?)?>'
    return re.escape(start_tag)


def create_tag_output_handler():
    tag_scan_positions = {}
    tag_boundary_positions = {}

    def tag_output_handler(content_type, tags, output):
        """
        Detect special tags (reasoning, solution, code_interpreter) in streaming
        content and create corresponding OR-aligned output items directly.
        Operates on output items instead of content_blocks.

        Uses the text from the output items themselves for tag detection,
        eliminating state divergence between accumulated content and items.

        Mutates output in place; returns the rewritten output (or None
        if no tag was consumed) and whether a block ended.
        """
        end_flag = False

        def extract_attributes(tag_content):
            """Extract attributes from a tag if they exist."""
            attributes = {}
            if not tag_content:
                return attributes
            matches = re.findall(r'(\w+)\s*=\s*"([^"]+)"', tag_content)
            for key, value in matches:
                attributes[key] = value
            return attributes

        def get_last_text(out):
            """Get text from last message item, or empty string."""
            if out and out[-1].get('type') == 'message':
                parts = out[-1].get('content', [])
                if parts and parts[-1].get('type') == 'output_text':
                    return parts[-1].get('text', '')
            return ''

        def set_last_text(out, text):
            """Set text on last message item's output_text."""
            if out and out[-1].get('type') == 'message':
                parts = out[-1].get('content', [])
                if parts and parts[-1].get('type') == 'output_text':
                    parts[-1]['text'] = text

        def get_scanned_length(item, text):
            item_id = item.get('id')
            if not item_id:
                return 0

            scanned_length = tag_scan_positions.get((item_id, content_type), 0)
            return scanned_length if scanned_length <= len(text) else 0

        def save_scanned_length(item, text):
            item_id = item.get('id')
            if item_id:
                tag_scan_positions[(item_id, content_type)] = len(text)

        def clear_scanned_length(item):
            item_id = item.get('id')
            if item_id:
                tag_scan_positions.pop((item_id, content_type), None)
                tag_boundary_positions.pop((item_id, content_type), None)

        def get_tag_boundaries(item, text, scanned_length):
            """Index of the last '<', and of the last '>' or newline, before scanned_length."""
            key = (item.get('id'), content_type)
            scanned, last_open, last_boundary = tag_boundary_positions.get(key, (0, -1, -1))
            if scanned > scanned_length:  # the item was rewritten, so the cached positions are stale
                scanned, last_open, last_boundary = 0, -1, -1

            if scanned < scanned_length:
                # only text added since the last call can move either position
                open_tag = text.rfind('<', scanned, scanned_length)
                if open_tag != -1:
                    last_open = open_tag
                boundary = max(
                    text.rfind('>', scanned, scanned_length),
                    text.rfind('\n', scanned, scanned_length),
                )
                if boundary != -1:
                    last_boundary = boundary
                tag_boundary_positions[key] = (scanned_length, last_open, last_boundary)

            return last_open, last_boundary

        # Map content_type to output item type
        output_type_map = {
            'reasoning': 'reasoning',
            'solution': 'message',  # solution tags just produce text
            'code_interpreter': 'open_webui:code_interpreter',
        }
        output_item_type = output_type_map.get(content_type, content_type)

        last_type = output[-1].get('type', '') if output else ''

        if last_type == 'message' and output[-1].get('_tag_type') != content_type:
            # Use the output item's own text for tag detection
            item = output[-1]
            item_text = get_last_text(output)
            scanned_length = get_scanned_length(item, item_text)
            max_start_tag_length = max((len(start_tag) for start_tag, _ in tags), default=1)
            search_start = max(0, scanned_length - max_start_tag_length + 1)

            if scanned_length and any(start_tag.startswith('<') and start_tag.endswith('>') for start_tag, _ in tags):
                open_tag_start, last_tag_boundary = get_tag_boundaries(item, item_text, scanned_length)
                if open_tag_start > last_tag_boundary:
                    search_start = min(search_start, open_tag_start)

            for start_tag, end_tag in tags:
                match = re.compile(_start_tag_pattern(start_tag)).search(item_text, search_start)
                if match:
                    clear_scanned_length(item)
                    try:
                        attr_content = match.group(1) if match.group(1) else ''
                    except Exception:
                        attr_content = ''

                    attributes = extract_attributes(attr_content)

                    before_tag = item_text[: match.start()]
                    after_tag = item_text[match.end() :]

                    # Keep only text before the tag in the message
                    set_last_text(output, before_tag)

                    if not before_tag.strip():
                        # Remove empty message item
                        if output and output[-1].get('type') == 'message':
                            output.pop()

                    # Append the new output item
                    if output_item_type == 'reasoning':
                        output.append(
                            {
                                'type': 'reasoning',
                                'id': output_id('r'),
                                'status': 'in_progress',
                                'start_tag': start_tag,
                                'end_tag': end_tag,
                                'attributes': attributes,
                                'content': [],
                                'summary': None,
                                'started_at': time.time(),
                            }
                        )
                    elif output_item_type == 'open_webui:code_interpreter':
                        output.append(
                            {
                                'type': 'open_webui:code_interpreter',
                                'id': output_id('ci'),
                                'status': 'in_progress',
                                'start_tag': start_tag,
                                'end_tag': end_tag,
                                'attributes': attributes,
                                'lang': attributes.get('lang', 'python'),
                                'code': '',
                                'output': None,
                                'started_at': time.time(),
                            }
                        )
                    else:
                        # solution or other text-producing tag
                        output.append(
                            {
                                'type': 'message',
                                'id': output_id('msg'),
                                'status': 'in_progress',
                                'role': 'assistant',
                                'content': [{'type': 'output_text', 'text': ''}],
                                '_tag_type': content_type,
                                'start_tag': start_tag,
                                'end_tag': end_tag,
                                'attributes': attributes,
                                'started_at': time.time(),
                            }
                        )

                    if after_tag:
                        # Set the after_tag content on the new item
                        if output_item_type == 'reasoning':
                            output[-1]['content'] = [{'type': 'output_text', 'text': after_tag}]
                        elif output_item_type == 'open_webui:code_interpreter':
                            output[-1]['code'] = after_tag
                        else:
                            set_last_text(output, after_tag)

                        _, recursive_end = tag_output_handler(content_type, tags, output)
                        if recursive_end:
                            end_flag = True

                    return output, end_flag
            else:
                save_scanned_length(item, item_text)

        elif (
            (last_type == 'reasoning' and content_type == 'reasoning')
            or (last_type == 'open_webui:code_interpreter' and content_type == 'code_interpreter')
            or (last_type == 'message' and output[-1].get('_tag_type') == content_type)
        ):
            item = output[-1]
            start_tag = item.get('start_tag', '')
            end_tag = item.get('end_tag', '')

            # Get the block content from the item itself
            if last_type == 'reasoning':
                parts = item.get('content', [])
                block_content = ''
                if parts and parts[-1].get('type') == 'output_text':
                    block_content = parts[-1].get('text', '')
            elif last_type == 'open_webui:code_interpreter':
                block_content = item.get('code', '')
            else:
                block_content = get_last_text(output)

            scanned_length = get_scanned_length(item, block_content)
            end_tag_search_start = max(0, scanned_length - max(len(end_tag), 1) + 1)

            if block_content.find(end_tag, end_tag_search_start) != -1:
                clear_scanned_length(item)
                end_flag = True

                # Strip start and end tags from content
                start_tag_pattern = _start_tag_pattern(start_tag)
                block_content = re.sub(start_tag_pattern, '', block_content)

                end_tag_pattern = rf'{re.escape(end_tag)}'
                end_tag_regex = re.compile(end_tag_pattern, re.DOTALL)
                split_content = end_tag_regex.split(block_content, maxsplit=1)

                block_content = split_content[0].strip() if split_content else ''
                leftover_content = split_content[1].lstrip() if len(split_content) > 1 else ''

                if block_content:
                    # Update the item with final content
                    if last_type == 'reasoning':
                        item['content'] = [{'type': 'output_text', 'text': block_content}]
                        item['ended_at'] = time.time()
                        item['duration'] = int(item['ended_at'] - item['started_at'])
                        item['status'] = 'completed'
                    elif last_type == 'open_webui:code_interpreter':
                        item['code'] = block_content
                        item['ended_at'] = time.time()
                        item['duration'] = int(item['ended_at'] - item['started_at'])
                    else:
                        set_last_text(output, block_content)
                        item['ended_at'] = time.time()

                    # Reset by appending a new message item for leftover
                    output.append(
                        {
                            'type': 'message',
                            'id': output_id('msg'),
                            'status': 'in_progress',
                            'role': 'assistant',
                            'content': [
                                {
                                    'type': 'output_text',
                                    'text': leftover_content,
                                }
                            ],
                        }
                    )
                else:
                    # Remove the block if content is empty
                    output.pop()
                    output.append(
                        {
                            'type': 'message',
                            'id': output_id('msg'),
                            'status': 'in_progress',
                            'role': 'assistant',
                            'content': [
                                {
                                    'type': 'output_text',
                                    'text': leftover_content,
                                }
                            ],
                        }
                    )
                return output, end_flag
            else:
                save_scanned_length(item, block_content)

        return None, False

    return tag_output_handler
