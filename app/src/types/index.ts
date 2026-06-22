// 二维码数据块类型
export interface QRChunk {
  filename: string;
  size: number;
  index: number;
  total: number;
  data: string; // Base64 encoded data
  checksum: number;
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
