import { useState, useCallback, useRef } from 'react';
import { QrCode, Smartphone, FileDown, HelpCircle } from 'lucide-react';
import { QRScanner } from '@/components/QRScanner';
import { FileAssembler } from '@/components/FileAssembler';
import { ScanStatusDisplay } from '@/components/ScanStatus';
import type { QRChunk, FileReceiveState, ScanStatus } from '@/types';
import { Toaster } from '@/components/ui/sonner';
import { toast } from 'sonner';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import './App.css';

function App() {
  // 扫描状态
  const [isScanning, setIsScanning] = useState(false);
  
  // 文件接收状态
  const [files, setFiles] = useState<Map<string, FileReceiveState>>(new Map());
  const [manualChunkText, setManualChunkText] = useState('');
  
  // 扫描状态消息
  const [scanStatus, setScanStatus] = useState<ScanStatus>({
    isScanning: false,
    lastScannedFilename: null,
    lastScannedChunk: null,
    totalChunks: null,
    message: '准备就绪，点击开始扫描',
    messageType: 'info',
  });

  // 用于去重扫描（防止同一二维码被多次处理）
  const scannedChunks = useRef<Set<string>>(new Set());

  // 处理扫描成功
  const handleScanSuccess = useCallback((decodedText: string) => {
    try {
      // 解析JSON数据
      const chunk: QRChunk = JSON.parse(decodedText);

      // 验证数据格式
      if (
        !chunk.filename ||
        typeof chunk.index !== 'number' ||
        typeof chunk.total !== 'number' ||
        chunk.index < 0 ||
        chunk.total <= 0 ||
        chunk.index >= chunk.total ||
        typeof chunk.data !== 'string'
      ) {
        setScanStatus({
          isScanning,
          lastScannedFilename: null,
          lastScannedChunk: null,
          totalChunks: null,
          message: '无效的二维码格式',
          messageType: 'error',
        });
        return;
      }

      // 创建唯一标识用于去重
      const chunkId = `${chunk.filename}_${chunk.index}`;
      
      // 检查是否已经扫描过这个块
      if (scannedChunks.current.has(chunkId)) {
        setScanStatus({
          isScanning,
          lastScannedFilename: chunk.filename,
          lastScannedChunk: chunk.index,
          totalChunks: chunk.total,
          message: '该块已扫描过',
          messageType: 'warning',
        });
        return;
      }

      // 标记为已扫描
      scannedChunks.current.add(chunkId);

      // 更新文件状态
      setFiles((prevFiles) => {
        const newFiles = new Map(prevFiles);
        const existingFile = newFiles.get(chunk.filename);

        if (existingFile) {
          // 更新现有文件
          existingFile.receivedChunks.set(chunk.index, chunk.data);
          existingFile.lastUpdated = Date.now();
          newFiles.set(chunk.filename, existingFile);
        } else {
          // 创建新文件记录
          const newFileState: FileReceiveState = {
            filename: chunk.filename,
            totalSize: chunk.size,
            totalChunks: chunk.total,
            receivedChunks: new Map([[chunk.index, chunk.data]]),
            lastUpdated: Date.now(),
          };
          newFiles.set(chunk.filename, newFileState);
        }

        return newFiles;
      });

      // 更新扫描状态
      const isComplete = chunk.index + 1 >= chunk.total;
      setScanStatus({
        isScanning,
        lastScannedFilename: chunk.filename,
        lastScannedChunk: chunk.index,
        totalChunks: chunk.total,
        message: isComplete ? '文件接收完成！' : `成功接收块 ${chunk.index + 1}/${chunk.total}`,
        messageType: 'success',
      });

      // 显示toast通知
      toast.success(
        `已接收: ${chunk.filename} (${chunk.index + 1}/${chunk.total})`,
        { duration: 2000 }
      );

    } catch (error) {
      console.error('解析二维码失败:', error);
      setScanStatus({
        isScanning,
        lastScannedFilename: null,
        lastScannedChunk: null,
        totalChunks: null,
        message: '无法解析二维码内容',
        messageType: 'error',
      });
    }
  }, [isScanning]);

  const handleManualChunkImport = useCallback(() => {
    const text = manualChunkText.trim();
    if (!text) {
      toast.warning('请先粘贴二维码内容');
      return;
    }
    handleScanSuccess(text);
    setManualChunkText('');
  }, [handleScanSuccess, manualChunkText]);

  // 切换扫描状态
  const toggleScanning = useCallback(() => {
    setIsScanning((prev) => {
      const newState = !prev;
      setScanStatus((status) => ({
        ...status,
        isScanning: newState,
        message: newState ? '正在扫描...' : '扫描已暂停',
        messageType: newState ? 'info' : 'warning',
      }));
      return newState;
    });
  }, []);

  // 清除单个文件
  const handleClearFile = useCallback((filename: string) => {
    setFiles((prevFiles) => {
      const newFiles = new Map(prevFiles);
      const fileState = newFiles.get(filename);
      if (fileState) {
        // 从已扫描集合中移除该文件的所有块
        for (let i = 0; i < fileState.totalChunks; i++) {
          scannedChunks.current.delete(`${filename}_${i}`);
        }
      }
      newFiles.delete(filename);
      return newFiles;
    });
    toast.info(`已删除: ${filename}`);
  }, []);

  // 清除所有文件
  const handleClearAll = useCallback(() => {
    setFiles(new Map());
    scannedChunks.current.clear();
    toast.info('已清除所有文件');
  }, []);

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <header className="border-b bg-card sticky top-0 z-10">
        <div className="container mx-auto px-4 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-primary rounded-lg">
                <QrCode className="w-6 h-6 text-primary-foreground" />
              </div>
              <div>
                <h1 className="text-xl font-bold">二维码文件扫描器</h1>
                <p className="text-xs text-muted-foreground">扫描文件二维码并还原为原始文件</p>
              </div>
            </div>
            <Dialog>
              <DialogTrigger asChild>
                <Button variant="ghost" size="icon">
                  <HelpCircle className="w-5 h-5" />
                </Button>
              </DialogTrigger>
              <DialogContent className="max-w-md">
                <DialogHeader>
                  <DialogTitle>使用说明</DialogTitle>
                  <DialogDescription>
                    如何使用此工具还原文件
                  </DialogDescription>
                </DialogHeader>
                <div className="space-y-4 text-sm">
                  <div className="flex gap-3">
                    <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0">
                      <span className="font-bold text-primary">1</span>
                    </div>
                    <div>
                      <p className="font-medium">在电脑上运行转换脚本</p>
                      <p className="text-muted-foreground">使用 file_to_qr.py 将文件转换为二维码图片</p>
                    </div>
                  </div>
                  <div className="flex gap-3">
                    <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0">
                      <span className="font-bold text-primary">2</span>
                    </div>
                    <div>
                      <p className="font-medium">在接收端打开此页面</p>
                      <p className="text-muted-foreground">
                        电脑与手机同网即可；若使用 HDMI 采集卡，请在电脑上打开本页，视频来源选「采集卡」并选中对应采集设备
                      </p>
                    </div>
                  </div>
                  <div className="flex gap-3">
                    <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0">
                      <span className="font-bold text-primary">3</span>
                    </div>
                    <div>
                      <p className="font-medium">点击「开始扫描」</p>
                      <p className="text-muted-foreground">
                        授予浏览器摄像头权限（采集卡在系统中会显示为视频设备）。采集卡模式为全画面识别，可避免手机摄像头对焦不准
                      </p>
                    </div>
                  </div>
                  <div className="flex gap-3">
                    <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0">
                      <span className="font-bold text-primary">4</span>
                    </div>
                    <div>
                      <p className="font-medium">按顺序扫描所有二维码</p>
                      <p className="text-muted-foreground">大文件会被分割成多个二维码，按顺序扫描即可</p>
                    </div>
                  </div>
                  <div className="flex gap-3">
                    <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0">
                      <span className="font-bold text-primary">5</span>
                    </div>
                    <div>
                      <p className="font-medium">下载还原的文件</p>
                      <p className="text-muted-foreground">当进度达到100%时，点击下载按钮</p>
                    </div>
                  </div>
                </div>
              </DialogContent>
            </Dialog>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="container mx-auto px-4 py-6">
        <div className="grid lg:grid-cols-2 gap-6">
          {/* Left Column - Scanner */}
          <div className="space-y-4">
            <QRScanner
              onScanSuccess={handleScanSuccess}
              isScanning={isScanning}
              onToggleScanning={toggleScanning}
            />

            {/* Scan Status */}
            <ScanStatusDisplay
              message={scanStatus.message}
              type={scanStatus.messageType}
              filename={scanStatus.lastScannedFilename}
              chunkIndex={scanStatus.lastScannedChunk}
              totalChunks={scanStatus.totalChunks}
            />

            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base">手动补录缺失分块</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <p className="text-sm text-muted-foreground">
                  如果某一张二维码始终无法自动识别，可用第三方扫码器读取后，把完整 JSON 内容粘贴到这里补齐。
                </p>
                <Textarea
                  value={manualChunkText}
                  onChange={(e) => setManualChunkText(e.target.value)}
                  placeholder='粘贴二维码文本，例如 {"filename":"a.txt","index":12,"total":163,...}'
                  className="min-h-28"
                />
                <Button onClick={handleManualChunkImport} className="w-full" variant="outline">
                  导入该分块
                </Button>
              </CardContent>
            </Card>
          </div>

          {/* Right Column - File Status */}
          <div>
            <FileAssembler
              files={files}
              onClearFile={handleClearFile}
              onClearAll={handleClearAll}
            />
          </div>
        </div>

        {/* Tips */}
        <div className="mt-8 grid md:grid-cols-3 gap-4">
          <div className="p-4 rounded-lg bg-muted/50">
            <div className="flex items-center gap-2 mb-2">
              <Smartphone className="w-5 h-5 text-primary" />
              <h3 className="font-medium">iOS 支持</h3>
            </div>
            <p className="text-sm text-muted-foreground">
              完全支持 iOS Safari 浏览器，无需安装任何应用
            </p>
          </div>
          <div className="p-4 rounded-lg bg-muted/50">
            <div className="flex items-center gap-2 mb-2">
              <QrCode className="w-5 h-5 text-primary" />
              <h3 className="font-medium">自动组装</h3>
            </div>
            <p className="text-sm text-muted-foreground">
              自动识别文件分块并按顺序组装，无需手动排序
            </p>
          </div>
          <div className="p-4 rounded-lg bg-muted/50">
            <div className="flex items-center gap-2 mb-2">
              <FileDown className="w-5 h-5 text-primary" />
              <h3 className="font-medium">批量下载</h3>
            </div>
            <p className="text-sm text-muted-foreground">
              支持单个文件下载或打包为ZIP批量下载
            </p>
          </div>
        </div>
      </main>

      {/* Footer */}
      <footer className="border-t mt-8 py-6">
        <div className="container mx-auto px-4 text-center text-sm text-muted-foreground">
          <p>二维码文件扫描器 | 安全传输，本地处理</p>
        </div>
      </footer>

      {/* Toast notifications */}
      <Toaster position="bottom-center" />
    </div>
  );
}

export default App;
