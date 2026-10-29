/** Update the visible text without replacing media, signatures or other output items. */
export function replaceOutputText(
	output: any[],
	index: number,
	field: 'content' | 'summary',
	text: string,
	defaultType: string
) {
	let replaced = false;
	const parts = (output[index][field] ?? []).map((part: any) => {
		if (!part || typeof part !== 'object' || !('text' in part || part.type === 'output_text'))
			return part;
		const next = { ...part, text: replaced ? '' : text };
		replaced = true;
		return next;
	});
	if (!replaced) parts.push({ type: defaultType, text });
	return output.map((item, i) => (i === index ? { ...item, [field]: parts } : item));
}
