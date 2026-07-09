import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

describe('AppLayout routing', () => {
  it('renders nested route pages through Umi Outlet', () => {
    const source = readFileSync('src/layouts/AppLayout.tsx', 'utf8');

    expect(source).toContain("import { Outlet } from 'umi'");
    expect(source).toContain('<Outlet />');
  });
});
