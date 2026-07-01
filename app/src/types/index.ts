// 二维码数据块类型(v2 协议:兼容 start/data/end 哨兵帧 + FEC 冗余帧)
export interface QRChunk {
  filename?: string;
  size?: number;
  index?: number;
  total?: number;
  data?: string; // Base64 encoded data
  checksum?: number;
  // v2 新增字段(可选)
  v?: number;          // 协议版本
  sid?: string;        // 会话 ID
  type?: 'start' | 'data' | 'end';  // 帧类型
  is_fec?: boolean;    // 是否为 FEC 冗余帧
  files?: any[];       // start 帧的文件清单
}

// 文件接收状态
export interface FileReceiveState {
  filename: string;
  totalSize: number;
  totalChunks: number;
  receivedChunks: Map<number, string>; // index -> data
  lastUpdated: number;
}

// 扫描状态
export interface ScanStatus {
  isScanning: boolean;
  lastScannedFilename: string | null;
  lastScannedChunk: number | null;
  totalChunks: number | null;
  message: string;
  messageType: 'info' | 'success' | 'error' | 'warning';
}
