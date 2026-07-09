import { Progress, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { taskStatusClass } from '@/utils/taskPayload';

export function MetricCards({ items }: { items: { label: string; value: string; note: string }[] }) {
  return (
    <div className="metric-grid">
      {items.map((item) => (
        <div className="metric-card" key={item.label}>
          <span>{item.label}</span>
          <strong>{item.value}</strong>
          <small>{item.note}</small>
        </div>
      ))}
    </div>
  );
}

export function Status({ value }: { value: string }) {
  return <span className={`status ${taskStatusClass(value)}`}>{value}</span>;
}

export function Platform({ value }: { value: string }) {
  return <span className="platform">{value}</span>;
}

export function DataTable<T extends object>({ columns, data }: { columns: ColumnsType<T>; data: T[] }) {
  return <Table rowKey={(record) => JSON.stringify(record)} columns={columns} dataSource={data} pagination={false} />;
}

export function Bar({ label, percent, value }: { label: string; percent: number; value: string }) {
  return (
    <div className="bar-row">
      <span>{label}</span>
      <Progress percent={percent} showInfo={false} strokeColor="var(--teal)" trailColor="#edf0f3" />
      <strong>{value}</strong>
    </div>
  );
}

export function Detail({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="detail-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function SmallTag({ value }: { value: string }) {
  return <Tag className="tag">{value}</Tag>;
}
