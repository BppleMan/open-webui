import json
import httpx

PROTOCOLS = ['openai-chat', 'openai-responses', 'anthropic', 'gemini', 'gemini-interactions', 'cpa-agy', 'ollama']


def text_response(protocol, pieces):
    if protocol == 'ollama':
        return httpx.Response(
            200,
            content=''.join(
                json.dumps({'message': {'role': 'assistant', 'content': piece}, 'done': i == len(pieces) - 1}) + '\n'
                for i, piece in enumerate(pieces)
            ),
        )
    if protocol in {'gemini', 'cpa-agy'}:
        frames = [
            {
                'candidates': [
                    {
                        'content': {'role': 'model', 'parts': [{'text': piece}]},
                        **({'finishReason': 'STOP'} if i == len(pieces) - 1 else {}),
                    }
                ]
            }
            for i, piece in enumerate(pieces)
        ]
    elif protocol == 'openai-chat':
        frames = [
            {
                'choices': [
                    {
                        'index': 0,
                        'delta': {'role': 'assistant', 'content': piece},
                        'finish_reason': 'stop' if i == len(pieces) - 1 else None,
                    }
                ]
            }
            for i, piece in enumerate(pieces)
        ]
    elif protocol == 'openai-responses':
        frames = [
            {
                'type': 'response.completed',
                'response': {
                    'id': 'synthetic-response',
                    'status': 'completed',
                    'output': [
                        {
                            'type': 'message',
                            'id': 'synthetic-message',
                            'role': 'assistant',
                            'content': [{'type': 'output_text', 'text': ''.join(pieces)}],
                        }
                    ],
                },
            }
        ]
    elif protocol == 'anthropic':
        frames = [
            {'type': 'message_start', 'message': {'id': 'synthetic-response', 'content': []}},
            {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}},
        ]
        frames += [
            {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': piece}}
            for piece in pieces
        ]
        frames += [
            {'type': 'content_block_stop', 'index': 0},
            {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}},
            {'type': 'message_stop'},
        ]
    elif protocol == 'gemini-interactions':
        frames = [
            {'event_type': 'interaction.created', 'interaction': {'id': 'synthetic-response', 'status': 'in_progress'}},
            {'event_type': 'step.start', 'index': 0, 'step': {'type': 'model_output', 'content': []}},
        ]
        frames += [
            {'event_type': 'step.delta', 'index': 0, 'delta': {'type': 'text', 'text': piece}} for piece in pieces
        ]
        frames += [
            {'event_type': 'step.stop', 'index': 0},
            {'event_type': 'interaction.completed', 'interaction': {'status': 'completed'}},
        ]
    return httpx.Response(200, content=''.join('data: ' + json.dumps(f) + '\n\n' for f in frames))
