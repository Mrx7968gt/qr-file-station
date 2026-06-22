import { useState, useCallback, useMemo } from 'react';
import { Download, FileText, Trash2, CheckCircle, AlertCircle, Package } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import type { FileReceiveState } from '@/types';
import JSZip from 'jszip';

interface FileAssemblerProps {
  files: Map<string, FileReceiveState>;
  onClearFile: (filename: string) => void;
  onClearAll: () => void;
}

export function FileAssembler({ files, onClearFile, onClearAll }: FileAssemblerProps) {
  const [downloading, setDownloading] = useState<string | null>(null);

  // 检查文件是否完整
  const isFileComplete = useCallback((fileState: FileReceiveState): boolean => {
    return fileState.receivedChunks.size === fileState.totalChunks;
  }, []);

  // 计算进度百分比
  const getProgress = useCallback((fileState: FileReceiveState): number => {
    return Math.round((fileState.receivedChunks.size / fileState.totalChunks) * 100);
  }, []);

  // 组装并下载单个文件
  const downloadFile = useCallback(async (filename: string, fileState: FileReceiveState) => {
    if (!isFileComplete(fileState)) return;

    setDownloading(filename);
    try {
      // 按顺序组装所有块
      const chunks: string[] = [];
      for (let i = 0; i < fileState.totalChunks; i++) {
        const chunk = fileState.receivedChunks.get(i);
        if (!chunk) {
          throw new Error(`缺少第 ${i + 1} 个数据块`);
        }
        chunks.push(chunk);
      }

      // 合并Base64数据
      const fullBase64 = chunks.join('');
      
      // 解码Base64为二进制
      const byteCharacters = atob(fullBase64);
      const byteNumbers = new Array(byteCharacters.length);
      for (let i = 0; i < byteCharacters.length; i++) {
        byteNumbers[i] = byteCharacters.charCodeAt(i);
      }
      const byteArray = new Uint8Array(byteNumbers);

      // 创建Blob并下载
      const blob = new Blob([byteArray]);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (error) {
      console.error('下载文件失败:', error);
      alert('下载文件失败: ' + (error as Error).message);
    } finally {
      setDownloading(null);
    }
  }, [isFileComplete]);

  // 下载所有完整文件为ZIP
  const downloadAllAsZip = useCallback(async () => {
    const completeFiles = Array.from(files.entries()).filter(([_, state]) => isFileComplete(state));
    if (completeFiles.length === 0) {
      alert('没有完整的文件可以下载');
      return;
    }

    setDownloading('__all__');
    try {
      const zip = new JSZip();

      for (const [filename, fileState] of completeFiles) {
        // 按顺序组装所有块
        const chunks: string[] = [];
        for (let i = 0; i < fileState.totalChunks; i++) {
          const chunk = fileState.receivedChunks.get(i);
          if (!chunk) continue;
          chunks.push(chunk);
        }

        // 解码Base64为二进制
        const fullBase64 = chunks.join('');
        const byteCharacters = atob(fullBase64);
        const byteNumbers = new Array(byteCharacters.length);
        for (let i = 0; i < byteCharacters.length; i++) {
          byteNumbers[i] = byteCharacters.charCodeAt(i);
        }
        const byteArray = new Uint8Array(byteNumbers);

        zip.file(filename, byteArray);
      }

      // 生成ZIP并下载
      const content = await zip.generateAsync({ type: 'blob' });
      const url = URL.createObjectURL(content);
      const link = document.createElement('a');
      link.href = url;
      link.download = `scanned_files_${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.zip`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (error) {
      console.error('下载ZIP失败:', error);
      alert('下载ZIP失败: ' + (error as Error).message);
    } finally {
      setDownloading(null);
    }
  }, [files, isFileComplete]);

  // 格式化文件大小
  const formatSize = useCallback((bytes: number): string => {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  }, []);

  // 格式化时间
  const formatTime = useCallback((timestamp: number): string => {
    return new Date(timestamp).toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  }, []);

  const fileList = useMemo(() => Array.from(files.entries()), [files]);
  const completeCount = useMemo(() => fileList.filter(([_, state]) => isFileComplete(state)).length, [fileList, isFileComplete]);

  if (files.size === 0) {
    return (
      <Card className="w-full">
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-lg">
            <FileText className="w-5 h-5" />
            文件接收状态
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-center py-8 text-muted-foreground">
            <Package className="w-12 h-12 mx-auto mb-3 opacity-50" />
            <p>暂无文件</p>
            <p className="text-sm mt-1">扫描二维码后将在此显示</p>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="w-full">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-lg">
            <FileText className="w-5 h-5" />
            文件接收状态
            <Badge variant="secondary" className="ml-2">
              {completeCount}/{files.size}
            </Badge>
          </CardTitle>
          {completeCount > 0 && (
            <Button
              variant="outline"
              size="sm"
              onClick={downloadAllAsZip}
              disabled={downloading === '__all__'}
            >
              <Package className="w-4 h-4 mr-1" />
              下载全部
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent>
        <ScrollArea className="h-[400px] pr-4">
          <div className="space-y-4">
            {fileList.map(([filename, fileState]) => {
              const complete = isFileComplete(fileState);
              const progress = getProgress(fileState);

              return (
                <div
                  key={filename}
                  className={`p-4 rounded-lg border ${
                    complete ? 'bg-green-50 border-green-200 dark:bg-green-950/20 dark:border-green-900' : 'bg-card'
                  }`}
                >
                  <div className="flex items-start justify-between mb-2">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="font-medium truncate" title={filename}>
                          {filename}
                        </p>
                        {complete ? (
                          <CheckCircle className="w-4 h-4 text-green-500 flex-shrink-0" />
                        ) : (
                          <AlertCircle className="w-4 h-4 text-amber-500 flex-shrink-0" />
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground mt-1">
                        大小: {formatSize(fileState.totalSize)} | 
                        块: {fileState.receivedChunks.size}/{fileState.totalChunks} | 
                        更新: {formatTime(fileState.lastUpdated)}
                      </p>
                    </div>
                    <div className="flex items-center gap-1 ml-2">
                      {complete && (
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          onClick={() => downloadFile(filename, fileState)}
                          disabled={downloading === filename}
                        >
                          <Download className="w-4 h-4" />
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8 text-destructive hover:text-destructive"
                        onClick={() => onClearFile(filename)}
                      >
                        <Trash2 className="w-4 h-4" />
                      </Button>
                    </div>
                  </div>

                  <div className="space-y-1">
                    <div className="flex justify-between text-xs">
                      <span className={complete ? 'text-green-600 dark:text-green-400' : 'text-muted-foreground'}>
                        {complete ? '已完成' : '接收中...'}
                      </span>
                      <span className="text-muted-foreground">{progress}%</span>
                    </div>
                    <Progress
                      value={progress}
                      className={`h-2 ${complete ? 'bg-green-200 dark:bg-green-900' : ''}`}
                    />
                  </div>

                  {/* 显示已接收的块 */}
                  {!complete && (
                    <div className="mt-3 flex flex-wrap gap-1">
                      {Array.from({ length: fileState.totalChunks }, (_, i) => (
                        <div
                          key={i}
                          className={`w-5 h-5 rounded text-[10px] flex items-center justify-center ${
                            fileState.receivedChunks.has(i)
                              ? 'bg-green-500 text-white'
                              : 'bg-muted text-muted-foreground'
                          }`}
                        >
                          {i + 1}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </ScrollArea>

        {files.size > 0 && (
          <div className="mt-4 pt-4 border-t">
            <Button
              variant="outline"
              size="sm"
              className="w-full text-destructive hover:text-destructive"
              onClick={onClearAll}
            >
              <Trash2 className="w-4 h-4 mr-2" />
              清除所有文件
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
