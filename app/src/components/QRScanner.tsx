import { useEffect, useRef, useState, useCallback } from 'react';
import { Html5Qrcode, Html5QrcodeSupportedFormats } from 'html5-qrcode';
import { Camera, ScanLine, AlertCircle, Monitor, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { cn } from '@/lib/utils';

type ScanSourceMode = 'camera' | 'capture';

interface QRScannerProps {
  onScanSuccess: (decodedText: string) => void;
  isScanning: boolean;
  onToggleScanning: () => void;
}

async function requestVideoPermission(): Promise<boolean> {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: true });
    stream.getTracks().forEach((t) => t.stop());
    return true;
  } catch {
    return false;
  }
}

async function listVideoInputs(): Promise<MediaDeviceInfo[]> {
  const devices = await navigator.mediaDevices.enumerateDevices();
  return devices.filter((d) => d.kind === 'videoinput');
}

export function QRScanner({ onScanSuccess, isScanning, onToggleScanning }: QRScannerProps) {
  const scannerRef = useRef<Html5Qrcode | null>(null);
  const scannerContainerId = 'qr-scanner-container';
  const [error, setError] = useState<string | null>(null);
  const [hasVideo, setHasVideo] = useState<boolean>(false);
  const [videoDevices, setVideoDevices] = useState<MediaDeviceInfo[]>([]);
  const [selectedDeviceId, setSelectedDeviceId] = useState<string>('');
  const [scanMode, setScanMode] = useState<ScanSourceMode>('capture');

  const refreshDevices = useCallback(async () => {
    const ok = await requestVideoPermission();
    if (!ok) {
      setHasVideo(false);
      setError(
        '无法访问视频设备，请授予摄像头权限，并通过 HTTPS 或 localhost 访问此页面'
      );
      return;
    }
    setHasVideo(true);
    setError(null);
    const list = await listVideoInputs();
    setVideoDevices(list);
    setSelectedDeviceId((prev) => {
      if (prev && list.some((d) => d.deviceId === prev)) return prev;
      return list[0]?.deviceId ?? '';
    });
  }, []);

  useEffect(() => {
    refreshDevices();
  }, [refreshDevices]);

  useEffect(() => {
    const onDeviceChange = () => {
      void refreshDevices();
    };
    navigator.mediaDevices.addEventListener('devicechange', onDeviceChange);
    return () =>
      navigator.mediaDevices.removeEventListener('devicechange', onDeviceChange);
  }, [refreshDevices]);

  const startScanning = useCallback(async () => {
    if (scanMode === 'capture' && !selectedDeviceId) {
      setError('请先选择采集卡对应的视频设备');
      return;
    }

    if (!scannerRef.current) {
      scannerRef.current = new Html5Qrcode(scannerContainerId, {
        formatsToSupport: [Html5QrcodeSupportedFormats.QR_CODE],
        verbose: false,
      });
    }

    const isCapture = scanMode === 'capture';

    const config = isCapture
      ? {
          fps: 15,
          aspectRatio: 16 / 9,
          qrbox: (viewfinderWidth: number, viewfinderHeight: number) => ({
            width: viewfinderWidth,
            height: viewfinderHeight,
          }),
          videoConstraints: {
            deviceId: { exact: selectedDeviceId },
            width: { ideal: 1920 },
            height: { ideal: 1080 },
            frameRate: { ideal: 30 },
          },
        }
      : {
          fps: 10,
          aspectRatio: 1,
          qrbox: { width: 250, height: 250 },
          videoConstraints: selectedDeviceId
            ? { deviceId: { exact: selectedDeviceId } }
            : undefined,
        };

    try {
      if (selectedDeviceId) {
        await scannerRef.current.start(
          selectedDeviceId,
          config,
          (decodedText) => {
            onScanSuccess(decodedText);
          },
          () => {}
        );
      } else {
        try {
          await scannerRef.current.start(
            { facingMode: 'environment' },
            config,
            (decodedText) => {
              onScanSuccess(decodedText);
            },
            () => {}
          );
        } catch {
          const devices = await Html5Qrcode.getCameras();
          if (devices.length === 0) {
            setError('未找到摄像头设备');
            return;
          }
          const rear = devices.find(
            (d) =>
              d.label.toLowerCase().includes('back') ||
              d.label.toLowerCase().includes('rear')
          );
          const cameraId = rear ? rear.id : devices[0].id;
          await scannerRef.current.start(
            cameraId,
            config,
            (decodedText) => {
              onScanSuccess(decodedText);
            },
            () => {}
          );
        }
      }
      setError(null);
    } catch (err) {
      setError('启动扫描失败: ' + (err as Error).message);
    }
  }, [onScanSuccess, selectedDeviceId, scanMode]);

  const stopScanning = useCallback(async () => {
    if (scannerRef.current && scannerRef.current.isScanning) {
      try {
        await scannerRef.current.stop();
      } catch (err) {
        console.error('停止扫描失败:', err);
      }
    }
  }, []);

  useEffect(() => {
    if (isScanning) {
      void startScanning();
    } else {
      void stopScanning();
    }

    return () => {
      if (!isScanning) {
        void stopScanning();
      }
    };
  }, [isScanning, startScanning, stopScanning]);

  useEffect(() => {
    return () => {
      if (scannerRef.current) {
        scannerRef.current.stop().catch(() => {});
        scannerRef.current = null;
      }
    };
  }, []);

  const deviceLabel = (d: MediaDeviceInfo, index: number) => {
    const name = d.label?.trim();
    if (name) return name;
    return `摄像头 ${index + 1}`;
  };

  return (
    <Card className="w-full">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-lg">
          <ScanLine className="w-5 h-5" />
          二维码扫描
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {!hasVideo && !error && (
          <Alert className="bg-amber-50 border-amber-200 dark:bg-amber-950/20 dark:border-amber-900">
            <AlertCircle className="h-4 w-4 text-amber-600" />
            <AlertDescription className="text-amber-800 dark:text-amber-200">
              正在检查视频设备...
            </AlertDescription>
          </Alert>
        )}

        <div className="space-y-2">
          <Label className="text-sm font-medium">视频来源</Label>
          <RadioGroup
            value={scanMode}
            onValueChange={(v) => setScanMode(v as ScanSourceMode)}
            disabled={isScanning}
            className="grid gap-2 sm:grid-cols-2"
          >
            <label
              className={cn(
                'flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors',
                scanMode === 'capture' && 'border-primary bg-primary/5',
                isScanning && 'opacity-60 pointer-events-none'
              )}
            >
              <RadioGroupItem value="capture" id="mode-capture" className="mt-0.5" />
              <div className="space-y-0.5">
                <div className="flex items-center gap-2 font-medium text-sm">
                  <Monitor className="w-4 h-4 shrink-0" />
                  采集卡 / 显示器画面
                </div>
                <p className="text-xs text-muted-foreground leading-snug">
                  选择 HDMI 采集卡设备，1080p 全画面识别，避免手机对焦问题
                </p>
              </div>
            </label>
            <label
              className={cn(
                'flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors',
                scanMode === 'camera' && 'border-primary bg-primary/5',
                isScanning && 'opacity-60 pointer-events-none'
              )}
            >
              <RadioGroupItem value="camera" id="mode-camera" className="mt-0.5" />
              <div className="space-y-0.5">
                <div className="flex items-center gap-2 font-medium text-sm">
                  <Camera className="w-4 h-4 shrink-0" />
                  本机摄像头
                </div>
                <p className="text-xs text-muted-foreground leading-snug">
                  小取景框扫描，适合笔记本或手机摄像头
                </p>
              </div>
            </label>
          </RadioGroup>
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <Label htmlFor="video-device" className="text-sm font-medium">
              视频设备
            </Label>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8 gap-1"
              onClick={() => void refreshDevices()}
              disabled={isScanning}
            >
              <RefreshCw className="w-3.5 h-3.5" />
              刷新列表
            </Button>
          </div>
          <Select
            value={selectedDeviceId || undefined}
            onValueChange={setSelectedDeviceId}
            disabled={isScanning || videoDevices.length === 0}
          >
            <SelectTrigger id="video-device" className="w-full">
              <SelectValue placeholder="选择视频输入设备" />
            </SelectTrigger>
            <SelectContent>
              {videoDevices.map((d, i) => (
                <SelectItem key={d.deviceId} value={d.deviceId}>
                  {deviceLabel(d, i)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">
            采集卡通常显示为「USB Video」「UVC」或厂商名称；手机投屏到显示器后由采集卡抓屏即可。
          </p>
        </div>

        <div className="relative">
          <div
            id={scannerContainerId}
            className={cn(
              'w-full mx-auto rounded-lg overflow-hidden bg-black',
              scanMode === 'capture'
                ? 'aspect-video max-w-[960px]'
                : 'aspect-square max-w-[400px]',
              isScanning ? 'block' : 'hidden'
            )}
          />

          {!isScanning && (
            <div
              className={cn(
                'w-full mx-auto rounded-lg bg-muted flex flex-col items-center justify-center border-2 border-dashed border-muted-foreground/25',
                scanMode === 'capture' ? 'aspect-video max-w-[960px]' : 'aspect-square max-w-[400px]'
              )}
            >
              <Camera className="w-16 h-16 text-muted-foreground mb-4" />
              <p className="text-muted-foreground text-center px-4 text-sm">
                {scanMode === 'capture' ? (
                  <>
                    选择采集卡设备后点击开始扫描
                    <br />
                    画面为显示器上的二维码，无需手机对焦
                  </>
                ) : (
                  <>
                    点击开始扫描
                    <br />
                    将二维码对准取景区域
                  </>
                )}
              </p>
            </div>
          )}

          {isScanning && (
            <div className="absolute inset-0 pointer-events-none">
              <div className="absolute top-1/2 left-0 right-0 h-0.5 bg-red-500/50 animate-scan-line" />
            </div>
          )}
        </div>

        <Button
          onClick={onToggleScanning}
          variant={isScanning ? 'destructive' : 'default'}
          className="w-full"
          disabled={!hasVideo || (scanMode === 'capture' && !selectedDeviceId)}
        >
          {isScanning ? '停止扫描' : '开始扫描'}
        </Button>

        <p className="text-xs text-muted-foreground text-center">
          {isScanning
            ? scanMode === 'capture'
              ? '全画面自动识别二维码（显示器画面稳定，无对焦模糊）'
              : '将二维码对准取景框即可自动识别'
            : '准备好后点击开始扫描'}
        </p>
      </CardContent>
    </Card>
  );
}
