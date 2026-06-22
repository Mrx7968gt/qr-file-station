import { CheckCircle, AlertCircle, Info, AlertTriangle } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';

interface ScanStatusProps {
  message: string;
  type: 'info' | 'success' | 'error' | 'warning';
  filename?: string | null;
  chunkIndex?: number | null;
  totalChunks?: number | null;
}

export function ScanStatusDisplay({
  message,
  type,
  filename,
  chunkIndex,
  totalChunks,
}: ScanStatusProps) {
  const icons = {
    info: <Info className="h-4 w-4" />,
    success: <CheckCircle className="h-4 w-4" />,
    error: <AlertCircle className="h-4 w-4" />,
    warning: <AlertTriangle className="h-4 w-4" />,
  };

  const variants = {
    info: 'default' as const,
    success: 'default' as const,
    error: 'destructive' as const,
    warning: 'default' as const,
  };

  return (
    <Alert variant={variants[type]} className={type === 'success' ? 'border-green-500 bg-green-50 dark:bg-green-950/20' : type === 'warning' ? 'border-amber-500 bg-amber-50 dark:bg-amber-950/20' : ''}>
      {icons[type]}
      <AlertTitle className="flex items-center gap-2">
        {message}
      </AlertTitle>
      {filename && (
        <AlertDescription className="mt-1">
          文件: <span className="font-medium">{filename}</span>
          {chunkIndex != null && totalChunks != null && totalChunks > 1 && (
            <span className="ml-2">
              (块 {(chunkIndex ?? 0) + 1}/{totalChunks})
            </span>
          )}
        </AlertDescription>
      )}
    </Alert>
  );
}
