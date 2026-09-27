/**
 * Copy non-TypeScript assets next to the compiled output so the server runs
 * purely from dist/: SQL schema, JSON config and the synthetic seed fixture.
 */
import { cpSync, mkdirSync, existsSync, rmSync, statSync } from 'node:fs';
import { dirname } from 'node:path';

const copies = [
  ['src/state/schema.sql', 'dist/src/state/schema.sql'],
  ['config/default.json', 'dist/config/default.json'],
  ['data/seed.json', 'dist/data/seed.json'],
  ['package.json', 'dist/package.json'],
  ['test/fixtures', 'dist/test/fixtures'],
];

for (const [from, to] of copies) {
  if (!existsSync(from)) {
    process.stderr.write(`missing asset: ${from}\n`);
    process.exit(1);
  }
  const isDirectory = statSync(from).isDirectory();
  if (isDirectory) {
    rmSync(to, { recursive: true, force: true });
  }
  mkdirSync(dirname(to), { recursive: true });
  cpSync(from, to, { recursive: isDirectory });
  process.stdout.write(`copied ${from} -> ${to}${isDirectory ? '/' : ''}\n`);
}
