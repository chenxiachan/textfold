import { readFile, readdir } from 'node:fs/promises';

export async function migrate(db) {
  for (const name of (await readdir('migrations')).filter(n => n.endsWith('.sql')).sort()) {
    const sql = (await readFile('migrations/' + name, 'utf8')).replace(/--[^\n]*/g, '');
    // Keep complete trigger bodies together; these migrations contain no SQL
    // string literals with semicolons or BEGIN/END nesting.
    const statements = sql.match(/\s*CREATE TRIGGER\b[\s\S]*?\bEND;|[^;]+;/gi) || [];
    await db.batch(statements.filter(s => s.trim()).map(s => db.prepare(s)));
  }
}
