import { Button, Card, Drawer, Form, Input, message, Modal, Popconfirm, Select, Space, Table } from 'antd';
import { useEffect, useMemo, useState } from 'react';
import { Detail, MetricCards, Platform, SmallTag, Status } from '@/components/Common';
import { api } from '@/services/api';
import type { PlatformAccount } from '@/types';

const platformOptions = ['微信公众号', '视频号', '小红书', '抖音'];

export default function Accounts() {
  const [form] = Form.useForm();
  const [accounts, setAccounts] = useState<PlatformAccount[]>([]);
  const [active, setActive] = useState<PlatformAccount | null>(null);
  const [authResult, setAuthResult] = useState<{ qr_url: string; account: PlatformAccount } | null>(null);
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

  async function submitAuth(values: { platform: string; account_name?: string }) {
    try {
      const data = await api.startAccountAuth(values);
      setAuthResult(data);
      await loadAccounts();
      message.success('账号已授权并同步近 30 天数据');
    } catch (error) {
      message.error(error instanceof Error ? error.message : '添加账号失败');
    }
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
            <Button type="primary" onClick={() => { form.resetFields(); setAuthResult(null); setAuthOpen(true); }}>添加账号</Button>
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
            { title: '标签', render: (_, row) => <SmallTag value={row.tag} /> },
            { title: '运营人', dataIndex: 'owner' },
            { title: '授权状态', render: (_, row) => <Status value={row.auth_status} /> },
            { title: '监测状态', render: (_, row) => <Status value={row.monitor_status} /> },
            { title: '最近同步', render: (_, row) => row.last_sync_at || '-' },
            {
              title: '操作',
              render: (_, row) => (
                <Space>
                  <Button type="link" onClick={() => setActive(row)}>详情</Button>
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

      <Modal centered title="添加账号" open={authOpen} onCancel={() => setAuthOpen(false)} footer={null}>
        <Form form={form} layout="vertical" onFinish={submitAuth} initialValues={{ platform: '微信公众号' }}>
          <Form.Item label="平台" name="platform" rules={[{ required: true }]}>
            <Select options={platformOptions.map((value) => ({ value }))} />
          </Form.Item>
          <Form.Item label="账号名称" name="account_name">
            <Input placeholder="可先填写账号名称，后续真实接入时扫码后自动回填" />
          </Form.Item>
          <p className="muted">点击后模拟弹出平台扫码授权二维码；授权成功后自动同步近 30 天账号和作品数据。</p>
          <Space>
            <Button type="primary" htmlType="submit">生成扫码授权</Button>
            <Button onClick={() => setAuthOpen(false)}>取消</Button>
          </Space>
        </Form>
        {authResult && (
          <div className="notice block-gap">
            <strong>扫码授权已完成</strong>
            <br />
            授权账号：{authResult.account.name}
            <br />
            授权入口：{authResult.qr_url}
          </div>
        )}
      </Modal>

      <Drawer title={active?.name} open={Boolean(active)} onClose={() => setActive(null)} width={560}>
        {active && (
          <div className="detail-grid">
            <Detail label="平台" value={active.platform} />
            <Detail label="账号名称" value={active.name} />
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
