import { Alert, Button, Card, Drawer, Form, Input, message, Modal, Popconfirm, Select, Space, Spin, Table } from 'antd';
import { useEffect, useMemo, useState } from 'react';
import { Detail, MetricCards, Platform, SmallTag, Status } from '@/components/Common';
import { api } from '@/services/api';
import type { AccountAuthSession, PlatformAccount } from '@/types';

const platformOptions = ['微信公众号', '视频号', '小红书', '抖音'];

export default function Accounts() {
  const [editForm] = Form.useForm();
  const [accounts, setAccounts] = useState<PlatformAccount[]>([]);
  const [active, setActive] = useState<PlatformAccount | null>(null);
  const [editing, setEditing] = useState<PlatformAccount | null>(null);
  const [authSession, setAuthSession] = useState<AccountAuthSession | null>(null);
  const [authLoading, setAuthLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [authOpen, setAuthOpen] = useState(false);
  const [platform, setPlatform] = useState('');
  const [authStatus, setAuthStatus] = useState('');
  const [search, setSearch] = useState('');

  async function loadAccounts() {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (platform) params.set('platform', platform);
      if (authStatus) params.set('auth_status', authStatus);
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
  }, [platform, authStatus]);

  async function startPlatformLogin(selectedPlatform: string) {
    setAuthLoading(true);
    try {
      const data = await api.startAccountAuth({ platform: selectedPlatform });
      setAuthSession(data);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '添加账号失败');
    } finally {
      setAuthLoading(false);
    }
  }

  useEffect(() => {
    if (!authSession || authSession.status !== 'waiting') return;
    const timer = window.setInterval(async () => {
      try {
        const data = await api.accountAuthStatus(authSession.session_id);
        setAuthSession(data);
        if (data.status === 'success') {
          window.clearInterval(timer);
          await loadAccounts();
          message.success('真实登录成功，账号已加入工作台');
        }
        if (data.status === 'failed' || data.status === 'cancelled') {
          window.clearInterval(timer);
        }
      } catch (error) {
        window.clearInterval(timer);
        const text = error instanceof Error ? error.message : '登录状态检测失败';
        setAuthSession((current) => current?.session_id === authSession.session_id
          ? { ...current, status: 'failed', message: text, error: text }
          : current);
        message.error(text);
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [authSession?.session_id, authSession?.status]);

  async function closeAuthModal() {
    if (authSession?.status === 'waiting') {
      try {
        await api.cancelAccountAuth(authSession.session_id);
      } catch {
        // 登录窗口可能已由用户关闭。
      }
    }
    setAuthOpen(false);
    setAuthSession(null);
  }

  async function syncAccount(account: PlatformAccount) {
    try {
      await api.syncAccount(account.id);
      await loadAccounts();
      message.success('账号数据已同步');
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
    const monitoring = accounts.filter((item) => item.monitor_status === '监测中').length;
    const expired = accounts.filter((item) => item.auth_status === '授权失效').length;
    return [
      { label: '已添加', value: String(accounts.length), note: '覆盖公众号、视频号、小红书、抖音' },
      { label: '已授权', value: String(authorized), note: '可用于定时任务发布' },
      { label: '监测中', value: String(monitoring), note: '每天自动同步，也可手动同步' },
      { label: '授权失效', value: String(expired), note: '需重新扫码授权' },
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
          </Space>
          <Space>
            <Button onClick={loadAccounts}>授权检测</Button>
            <Button onClick={() => Promise.all(accounts.map((item) => syncAccount(item))).then(loadAccounts)}>更新账号信息</Button>
            <Button type="primary" onClick={() => { setAuthSession(null); setAuthOpen(true); }}>添加账号</Button>
          </Space>
        </div>
        <Table
          loading={loading}
          rowKey="id"
          pagination={false}
          dataSource={accounts}
          columns={[
            { title: '账号', render: (_, row) => <><strong>{row.name}</strong><br /><span className="muted">{row.role}</span></> },
            { title: '平台', render: (_, row) => <Platform value={row.platform} /> },
            { title: '标签', render: (_, row) => <SmallTag value={row.tag || '未设置'} /> },
            { title: '运营人', render: (_, row) => row.owner || '未分配' },
            { title: '授权状态', render: (_, row) => <Status value={row.auth_status} /> },
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

      <Modal centered title="添加账号" open={authOpen} onCancel={closeAuthModal} footer={null} width={680}>
        {!authSession ? (
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
                <Button type="primary" onClick={() => setAuthSession(null)}>重新选择平台</Button>
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
            <Detail label="监测状态" value={active.monitor_status} />
            <Detail label="同步状态" value={active.sync_status} />
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
