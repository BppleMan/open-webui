"""Behavioral regressions at the v0.12.5/customization merge boundary."""

from open_webui.utils.response import update_usage_snapshot, merge_usage


def test_repeated_partial_provider_usage_is_one_call_then_tools_are_additive():
    first = update_usage_snapshot({}, {'prompt_tokens': 123, 'input_tokens_details': {'cached_tokens': 0}})
    duplicate = update_usage_snapshot(first, {'prompt_tokens': 123})
    assert duplicate == first
    completed = update_usage_snapshot(duplicate, {'completion_tokens': 7})
    assert completed['input_tokens'] == 123 and completed['output_tokens'] == 7
    assert completed['total_tokens'] == 130
    assert completed['input_tokens_details']['cached_tokens'] == 0
    assert 'output_tokens' not in first  # interrupted stream stays unknown
    second = update_usage_snapshot({}, {'input_tokens': 10, 'output_tokens': 2})
    combined = merge_usage(completed, second)
    assert combined['input_tokens'] == 133 and combined['output_tokens'] == 9
    assert combined['total_tokens'] == 142
    assert combined['prompt_tokens'] == 10  # latest request size is separate from totals
