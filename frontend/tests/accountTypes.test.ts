import { describe, expect, it } from 'vitest';
import type { AccountAuthSession, PlatformAccount } from '../src/types';

type PublicPlatformAccountKey =
  | 'id'
  | 'platform'
  | 'name'
  | 'external_id'
  | 'owner'
  | 'tag'
  | 'auth_status'
  | 'login_status'
  | 'monitor_status'
  | 'sync_status'
  | 'sync_error'
  | 'role'
  | 'audience'
  | 'style'
  | 'taboo'
  | 'preference'
  | 'auth_method'
  | 'last_sync_at'
  | 'created_at'
  | 'updated_at';

type NonPublicPlatformAccountKey = Exclude<keyof PlatformAccount, PublicPlatformAccountKey>;

describe('PlatformAccount public contract', () => {
  it('contains only explicitly public account fields', () => {
    const hasNoNonPublicKeys: NonPublicPlatformAccountKey extends never ? true : false = true;
    expect(hasNoNonPublicKeys).toBe(true);
  });

  it('uses the auth session account projection returned by the backend', () => {
    type AuthAccount = NonNullable<AccountAuthSession['account']>;
    type AuthAccountKey =
      | 'id'
      | 'platform'
      | 'name'
      | 'account_name'
      | 'auth_status'
      | 'login_status'
      | 'monitor_status'
      | 'sync_status'
      | 'sync_error'
      | 'last_sync_at';
    type NonAuthAccountKey = Exclude<keyof AuthAccount, AuthAccountKey>;
    const hasNoUnexpectedAuthKeys: NonAuthAccountKey extends never ? true : false = true;
    expect(hasNoUnexpectedAuthKeys).toBe(true);
  });
});
