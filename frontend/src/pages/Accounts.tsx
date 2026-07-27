import { Alert, Button, Card, Drawer, Form, Input, message, Modal, Popconfirm, Select, Space, Spin, Table } from 'antd';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Detail, MetricCards, Platform, SmallTag, Status } from '@/components/Common';
import { api } from '@/services/api';
import type { AccountAuthSession, PlatformAccount } from '@/types';

const platformOptions = ['微信公众号', '视频号', '小红书', '抖音'];

function loginStatusText(account: PlatformAccount) {
  return account.login_status === '在线' ? '在线' : '离线';
}

export default function Accounts() {
  const [editForm] = Form.useForm();
  const [accounts, setAccounts] = useState<PlatformAccount[]>([]);
  const [active, setActive] = useState<PlatformAccount | null>(null);
  const [editing, setEditing] = useState<PlatformAccount | null>(null);
  const [authSession, setAuthSession] = useState<AccountAuthSession | null>(null);
  const [authTarget, setAuthTarget] = useState<PlatformAccount | null>(null);
  const [authLoading, setAuthLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [authOpen, setAuthOpen] = useState(false);
  const [platform, setPlatform] = useState('');
  const [authStatus, setAuthStatus] = useState('');
  const [loginStatus, setLoginStatus] = useState('');
  const [search, setSearch] = useState('');
  const authGenerationRef = useRef(0);
  const authSessionIdRef = useRef<string | null>(null);

  async function loadAccounts() {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (platform) params.set('platform', platform);
      if (authStatus) params.set('auth_status', authStatus);
      if (loginStatus) params.set('login_status', loginStatus);
      if (search) params.set('q', search);
      const data = await api.accounts(params);
      setAccounts(data.accounts || []);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '读取账号失败');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadAccounts();
  }, [platform, authStatus, loginStatus]);

  async function startPlatformLogin(selectedPlatform: string, target?: PlatformAccount) {
    const generation = authGenerationRef.current + 1;
    authGenerationRef.current = generation;
    authSessionIdRef.current = null;
    setAuthTarget(target || null);
    setAuthSession(null);
    setAuthLoading(true);
    try {
      const data = await api.startAccountAuth({
        platform: selectedPlatform,
        account_name: target?.name,
        account_id: target?.id,
      });
      if (authGenerationRef.current !== generation) return;
      authSessionIdRef.current = data.session_id;
      setAuthSession(data);
    } catch (error) {
      if (authGenerationRef.current !== generation) return;
      message.error(error instanceof Error ? error.message : '添加账号失败');
    } finally {
      if (authGenerationRef.current === generation) setAuthLoading(false);
    }
  }

  useEffect(() => {
    if (!authSession || authSession.status !== 'waiting') return;
    const generation = authGenerationRef.current;
    const sessionId = authSession.session_id;
    let active = true;
    let polling = false;
    const ownsSession = () => active
      && authGenerationRef.current === generation
      && authSessionIdRef.current === sessionId;
    const timer = window.setInterval(async () => {
      if (polling || !ownsSession()) return;
      polling = true;
      try {
        const data = await api.accountAuthStatus(sessionId);
        if (!ownsSession() || data.session_id !== sessionId) return;
        setAuthSession(data);
        if (data.status === 'success') {
          window.clearInterval(timer);
          setAuthOpen(false);
          setAuthSession(null);
          setAuthTarget(null);
          await loadAccounts();
          if (ownsSession()) message.success('真实登录成功，账号状态已更新');
        }
        if (data.status === 'failed' || data.status === 'cancelled') {
          window.clearInterval(timer);
        }
      } catch (error) {
        if (!ownsSession()) return;
        window.clearInterval(timer);
        const text = error instanceof Error ? error.message : '登录状态检测失败';
        setAuthSession((current) => current?.session_id === sessionId
          ? { ...current, status: 'failed', message: text, error: text }
          : current);
        message.error(text);
      } finally {
        polling = false;
      }
    }, 2000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [authSession?.session_id, authSession?.status]);

  async function closeAuthModal() {
    const session = authSession;
    authGenerationRef.current += 1;
    authSessionIdRef.current = null;
    setAuthOpen(false);
    setAuthSession(null);
    setAuthTarget(null);
    setAuthLoading(false);
    if (session?.status === 'waiting') {
      try {
        await api.cancelAccountAuth(session.session_id);
      } catch {
        // 登录窗口可能已由用户关闭。
      }
    }
  }

  function openAuthModal() {
    authGenerationRef.current += 1;
    authSessionIdRef.current = null;
    setAuthSession(null);
    setAuthTarget(null);
    setAuthLoading(false);
    setAuthOpen(true);
  }

  function resetAuthPlatform() {
    authGenerationRef.current += 1;
    authSessionIdRef.current = null;
    setAuthSession(null);
    setAuthTarget(null);
    setAuthLoading(false);
  }

  function openAccount(account: PlatformAccount) {
    if (loginStatusText(account) === '离线') {
      setAuthOpen(true);
      void startPlatformLogin(account.platform, account);
      return;
    }
    setActive(account);
  }

  async function syncAccount(account: PlatformAccount) {
    try {
      await api.syncAccount(account.id);
      await loadAccounts();
      message.success('账号数据已同步到数据分析');
    } catch (error) {
      message.error(error instanceof Error ? error.message : '同步失败');
    }
  }

  async function deleteAccount(account: PlatformAccount) {
    try {
      await api.deleteAccount(account.id);
      await loadAccounts();
    } catch (error) {
      message.error(error instanceof Error ? error.message : '删除失败');
    }
  }

  function openEdit(account: PlatformAccount) {
    setEditing(account);
    editForm.setFieldsValue({
      name: account.name,
      external_id: account.external_id,
      tag: account.tag,
      owner: account.owner,
    });
  }

  async function saveAccount(values: Partial<PlatformAccount>) {
    if (!editing) return;
    try {
      await api.updateAccount(editing.id, {
        ...editing,
        ...values,
      });
      setEditing(null);
      await loadAccounts();
      message.success('账号管理信息已更新');
    } catch (error) {
      message.error(error instanceof Error ? error.message : '账号信息更新失败');
    }
  }

  const metrics = useMemo(() => {
    const authorized = accounts.filter((item) => item.auth_status === '已授权').length;
    const online = accounts.filter((item) => loginStatusText(item) === '在线').length;
    const offline = accounts.filter((item) => loginStatusText(item) === '离线').length;
    const monitoring = accounts.filter((item) => item.monitor_status === '监测中').length;
    return [
      { label: '已添加', value: String(accounts.length), note: '覆盖公众号、视频号、小红书、抖音' },
      { label: '已授权', value: String(authorized), note: '可用于定时任务发布' },
      { label: '在线账号', value: String(online), note: '可直接执行发布和同步' },
      { label: '离线账号', value: String(offline), note: '点击账号可重新扫码' },
      { label: '监测中', value: String(monitoring), note: '每天自动同步，也可手动同步' },
    ];
  }, [accounts]);

  return (
    <>
      <MetricCards items={metrics} />
      <Card className="panel">
        <div className="toolbar">
          <Space wrap>
            <Input.Search
              placeholder="搜索账号名称 / 标签 / 运营人"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              onSearch={loadAccounts}
              style={{ width: 240 }}
            />
            <Select
              value={platform}
              style={{ width: 150 }}
              onChange={setPlatform}
              options={['', ...platformOptions].map((value) => ({ value, label: value || '全部平台' }))}
            />
            <Select
              value={authStatus}
              style={{ width: 160 }}
              onChange={setAuthStatus}
              options={['', '已授权', '授权失效', '未授权'].map((value) => ({ value, label: value || '全部授权状态' }))}
            />
            <Select
              value={loginStatus}
              style={{ width: 160 }}
              onChange={setLoginStatus}
              options={['', '在线', '离线'].map((value) => ({ value, label: value || '全部登录状态' }))}
            />
          </Space>
          <Space>
            <Button onClick={loadAccounts}>授权检测</Button>
            <Button onClick={() => Promise.all(accounts.map((item) => syncAccount(item))).then(loadAccounts)}>更新账号信息</Button>
            <Button type="primary" onClick={openAuthModal}>添加账号</Button>
          </Space>
        </div>
        <Table
          loading={loading}
          rowKey="id"
          pagination={false}
          dataSource={accounts}
          columns={[
            {
              title: '账号',
              render: (_, row) => (
                <>
                  <Button type="link" className="inline-link" onClick={() => openAccount(row)}>{row.name}</Button>
                  <br />
                  <span className="muted">{row.role}</span>
                </>
              ),
            },
            { title: '平台', render: (_, row) => <Platform value={row.platform} /> },
            { title: '标签', render: (_, row) => <SmallTag value={row.tag || '未设置'} /> },
            { title: '运营人', render: (_, row) => row.owner || '未分配' },
            { title: '授权状态', render: (_, row) => <Status value={row.auth_status} /> },
            { title: '登录状态', render: (_, row) => <Status value={loginStatusText(row)} /> },
            { title: '监测状态', render: (_, row) => <Status value={row.monitor_status} /> },
            { title: '最近同步', render: (_, row) => row.last_sync_at || '-' },
            {
              title: '操作',
              render: (_, row) => (
                <Space>
                  <Button type="link" onClick={() => setActive(row)}>详情</Button>
                  <Button type="link" onClick={() => openEdit(row)}>编辑</Button>
                  <Button type="link" onClick={() => syncAccount(row)}>同步</Button>
                  <Popconfirm title={`确定删除账号「${row.name}」吗？`} onConfirm={() => deleteAccount(row)}>
                    <Button type="link" danger>删除</Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Modal centered title={authTarget ? `重新登录：${authTarget.name}` : '添加账号'} open={authOpen} onCancel={closeAuthModal} footer={null} width={680} destroyOnHidden>
        {!authSession && authTarget ? (
          <Spin spinning={authLoading}>
            <Alert
              showIcon
              type="info"
              message={`正在打开${authTarget.platform}扫码登录窗口`}
              description="完成扫码后，工作台会把该账号状态更新为在线，并自动关闭登录弹窗。"
            />
          </Spin>
        ) : !authSession ? (
          <>
            <p className="muted">选择平台后将自动打开独立的真实 Chrome 登录窗口，请使用对应平台 App 扫码。</p>
            <div className="platform-login-grid">
              {platformOptions.map((value) => (
                <Button key={value} className="platform-login-option" loading={authLoading} onClick={() => startPlatformLogin(value)}>
                  <strong>{value}</strong>
                  <span>真实网页登录</span>
                </Button>
              ))}
            </div>
          </>
        ) : (
          <Spin spinning={authSession.status === 'waiting'}>
            <Alert
              showIcon
              type={authSession.status === 'success' ? 'success' : authSession.status === 'waiting' ? 'info' : 'error'}
              message={authSession.status === 'waiting' ? `等待${authSession.platform}扫码登录` : authSession.message}
              description={authSession.status === 'waiting'
                ? '登录页已在独立 Chrome 窗口打开。扫码并完成平台确认后，工作台会自动识别登录结果。'
                : authSession.account ? `已登录账号：${authSession.account.name}` : authSession.error}
            />
            <Space className="block-gap">
              {authSession.status === 'success' && <Button type="primary" onClick={closeAuthModal}>完成</Button>}
              {authSession.status !== 'success' && <Button onClick={closeAuthModal}>取消</Button>}
              {(authSession.status === 'failed' || authSession.status === 'cancelled') && (
                authTarget
                  ? <Button type="primary" onClick={() => startPlatformLogin(authTarget.platform, authTarget)}>重新扫码</Button>
                  : <Button type="primary" onClick={resetAuthPlatform}>重新选择平台</Button>
              )}
            </Space>
          </Spin>
        )}
      </Modal>

      <Modal
        centered
        title="编辑账号信息"
        open={Boolean(editing)}
        onCancel={() => setEditing(null)}
        onOk={() => editForm.submit()}
        okText="保存"
      >
        <Form form={editForm} layout="vertical" onFinish={saveAccount}>
          <Form.Item label="账号名称" name="name" rules={[{ required: true, message: '请输入账号名称' }]}>
            <Input disabled />
          </Form.Item>
          <Form.Item label="平台账号 ID" name="external_id">
            <Input disabled />
          </Form.Item>
          <Form.Item label="账号标签" name="tag">
            <Input placeholder="例如：财税科普、企业服务" />
          </Form.Item>
          <Form.Item label="运营人" name="owner">
            <Input placeholder="请输入实际负责该账号的人员" />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer title={active?.name} open={Boolean(active)} onClose={() => setActive(null)} width={560}>
        {active && (
          <div className="detail-grid">
            <Detail label="平台" value={active.platform} />
            <Detail label="账号名称" value={active.name} />
            <Detail label="平台账号 ID" value={active.external_id || '-'} />
            <Detail label="运营人" value={active.owner} />
            <Detail label="账号标签" value={active.tag} />
            <Detail label="授权状态" value={active.auth_status} />
            <Detail label="登录状态" value={loginStatusText(active)} />
            <Detail label="监测状态" value={active.monitor_status} />
            <Detail label="同步状态" value={active.sync_status} />
            <Detail label="同步错误" value={active.sync_error || '-'} />
            <Detail label="最近同步" value={active.last_sync_at || '-'} />
            <Detail label="角色定位" value={active.role} />
            <Detail label="面向用户" value={active.audience} />
            <Detail label="内容风格" value={active.style} />
            <Detail label="禁忌方向" value={active.taboo} />
            <Detail label="AI 动态偏好" value={active.preference} />
          </div>
        )}
      </Drawer>
    </>
  );
}
