/**
 * Tool rows at the level you read at.
 *
 * Pi draws a tool call and its result as components, not Markdown, so the fold's
 * markdown transformer never sees them. A renderer registered with
 * `pi.registerToolRenderer` does: it can hand back the built-in renderer's own
 * component wrapped in one that keeps fewer of its lines, and it can read the
 * level when the row is drawn (a level change invalidates the screen, which is
 * what re-renders the row).
 *
 * The rule is the fold's rule: remove, never invent. Full and Brief are Pi's own
 * row; Points keeps the first lines and names the rest; Outline and Gist keep the
 * call line and no result body, so the turn still reads as what happened.
 */

/** Result lines kept at a level: Pi's own row at Full and Brief, two at Points,
 *  none at Outline and Gist. An unknown level keeps everything: a corrupt level
 *  must not hide output. */
export function toolResultKeep(level: number): number {
	if (level <= 2) return 5; // Pi's own preview (bash: 5, read: 10) is Brief already
	if (level === 3) return 2;
	if (level === 4 || level === 5) return 0;
	return 5;
}

const ANSI = /\x1b\[[0-9;]*m/g;

/**
 * The result lines to draw at `level`.
 *
 * Levels 1 and 2 return the very array Pi's renderer produced, so the row is
 * byte-identical to Pi's own. Above that, the blank lines a component pads with
 * are dropped and only non-empty lines count: hiding output must not leave the
 * height the output took. Whatever is left out is named in one marker line, the
 * same "… (N more lines)" the fold uses.
 */
export function foldToolLines(lines: readonly string[], level: number): string[] {
	if (level <= 2) return lines as string[];
	const keep = toolResultKeep(level);
	const content = lines.filter((line) => line.replace(ANSI, "").trim() !== "");
	if (keep === 0) return [];
	const shown = content.slice(0, keep);
	const hidden = content.length - shown.length;
	return hidden > 0 ? [...shown, `… (${hidden} more line${hidden === 1 ? "" : "s"} · Alt+1)`] : shown;
}
