import { describe, expect, it } from 'vitest';
import type { PlatformAccount } from '../src/types';

type PublicPlatformAccountKey =
  | 'id'
  | 'platform'
  | 'name'
  | 'external_id'
  | 'owner'
  | 'tag'
  | 'auth_status'
  | 'monitor_status'
  | 'sync_status'
  | 'sync_error'
  | 'role'
  | 'audience'
  | 'style'
  | 'taboo'
  | 'preference'
  | 'auth_method'
  | 'qr_url'
  | 'last_sync_at'
  | 'created_at'
  | 'updated_at';

type NonPublicPlatformAccountKey = Exclude<keyof PlatformAccount, PublicPlatformAccountKey>;

describe('PlatformAccount public contract', () => {
  it('contains only explicitly public account fields', () => {
    const hasNoNonPublicKeys: NonPublicPlatformAccountKey extends never ? true : false = true;
    expect(hasNoNonPublicKeys).toBe(true);
  });
});
