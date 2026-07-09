import { App } from 'antd';
import type React from 'react';
import '@/global.css';

export function rootContainer(container: React.ReactNode) {
  return <App>{container}</App>;
}
