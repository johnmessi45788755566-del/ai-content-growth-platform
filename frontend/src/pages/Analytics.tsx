import { Card, Empty, Input, message, Select, Space, Tabs, Table } from 'antd';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { MetricCards, Platform, Status } from '@/components/Common';
import { api } from '@/services/api';
import type { AccountAnalyticsRow, WorkAnalyticsRow } from '@/types';

const platformOptions = ['', '微信公众号', '视频号', '小红书', '抖音'];

function formatNumber(value?: number) {
  return Number(value || 0).toLocaleString('zh-CN');
}

export default function Analytics() {
  const [accountRows, setAccountRows] = useState<AccountAnalyticsRow[]>([]);
  const [workRows, setWorkRows] = useState<WorkAnalyticsRow[]>([]);
  const [activeTab, setActiveTab] = useState<'account' | 'work'>('account');
  const [platform, setPlatform] = useState('');
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(false);

  const loadAnalytics = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ range: '30d' });
      if (platform) params.set('platform', platform);
      if (search) params.set('q', search);
      const [accountsData, worksData] = await Promise.all([
        api.analyticsAccounts(params),
        api.analyticsWorks(params),
      ]);
      setAccountRows(accountsData.accounts || []);
      setWorkRows(worksData.works || []);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '读取数据失败');
    } finally {
      setLoading(false);
    }
  }, [platform, search]);

  useEffect(() => {
    loadAnalytics();
  }, [platform]);

  useEffect(() => {
    function handleRefresh() {
      void loadAnalytics();
    }

    window.addEventListener('analytics-refresh', handleRefresh);
    window.addEventListener('storage', handleRefresh);

    return () => {
      window.removeEventListener('analytics-refresh', handleRefresh);
      window.removeEventListener('storage', handleRefresh);
    };
  }, [loadAnalytics]);

  const summary = useMemo(() => {
    const reads = accountRows.reduce((sum, item) => sum + Number(item.reads || 0), 0);
    const recommends = accountRows.reduce((sum, item) => sum + Number(item.recommends || 0), 0);
    const interactions = accountRows.reduce(
      (sum, item) => sum + Number(item.likes || 0) + Number(item.comments || 0) + Number(item.shares || 0) + Number(item.favorites || 0),
      0,
    );
    return [
      { label: '总推荐数', value: formatNumber(recommends), note: '近 30 天平台推荐' },
      { label: '总阅读 / 播放', value: formatNumber(reads), note: '近 30 天消费数据' },
      { label: '总互动', value: formatNumber(interactions), note: '点赞、评论、转发、收藏' },
      { label: '同步账号', value: String(accountRows.length), note: '来自账号矩阵授权账号' },
    ];
  }, [accountRows]);

  return (
    <>
      <div className="toolbar">
        <Tabs
          activeKey={activeTab}
          onChange={(key) => setActiveTab(key as 'account' | 'work')}
          items={[
            { key: 'account', label: '账号数据' },
            { key: 'work', label: '作品数据' },
          ]}
        />
        <Space wrap>
          <Select value="近 30 天" options={[{ value: '近 30 天', label: '近 30 天' }]} />
          <Select value={platform} onChange={setPlatform} options={platformOptions.map((value) => ({ value, label: value || '全部平台' }))} />
          <Input.Search placeholder="搜索账号或作品" value={search} onChange={(event) => setSearch(event.target.value)} onSearch={loadAnalytics} />
        </Space>
      </div>
      {activeTab === 'account' ? (
        <>
          <MetricCards items={summary} />
          <Card className="panel">
            <Table
              loading={loading}
              rowKey="account_id"
              pagination={false}
              locale={{ emptyText: <Empty description="暂无账号数据，请先在账号矩阵添加并同步账号" /> }}
              dataSource={accountRows}
              columns={[
                { title: '账号', dataIndex: 'account_name' },
                { title: '平台', render: (_, row) => <Platform value={row.platform} /> },
                { title: '粉丝数', render: (_, row) => formatNumber(row.fans) },
                { title: '阅读 / 播放', render: (_, row) => formatNumber(row.reads) },
                { title: '推荐数', render: (_, row) => formatNumber(row.recommends) },
                { title: '点赞', render: (_, row) => formatNumber(row.likes) },
                { title: '评论', render: (_, row) => formatNumber(row.comments) },
                { title: '转发', render: (_, row) => formatNumber(row.shares) },
                { title: '收藏', render: (_, row) => formatNumber(row.favorites) },
                { title: '更新时间', render: (_, row) => row.last_sync_at || '-' },
              ]}
            />
          </Card>
        </>
      ) : (
        <Card className="panel" title="作品数据">
          <Table
            loading={loading}
            rowKey="work_id"
            pagination={false}
            locale={{ emptyText: <Empty description="暂无作品数据，请先同步账号数据" /> }}
            dataSource={workRows}
            columns={[
              { title: '作品标题', dataIndex: 'title' },
              { title: '平台', render: (_, row) => <Platform value={row.platform} /> },
              { title: '账号', dataIndex: 'account_name' },
              { title: '类型', dataIndex: 'content_type' },
              { title: '阅读 / 播放', render: (_, row) => formatNumber(row.reads) },
              { title: '推荐数', render: (_, row) => formatNumber(row.recommends) },
              { title: '点赞', render: (_, row) => formatNumber(row.likes) },
              { title: '评论', render: (_, row) => formatNumber(row.comments) },
              { title: '转发', render: (_, row) => formatNumber(row.shares) },
              { title: '收藏', render: (_, row) => formatNumber(row.favorites) },
              { title: '状态', render: (_, row) => <Status value={row.status} /> },
            ]}
          />
        </Card>
      )}
    </>
  );
}
