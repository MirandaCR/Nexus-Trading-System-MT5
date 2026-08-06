'use client';

import { cn } from '@/lib/utils';

interface PulseIndicatorProps {
  active: boolean;
  color?: 'green' | 'red' | 'purple' | 'cyan' | 'gold';
  size?: 'sm' | 'md' | 'lg';
}

const sizeMap = {
  sm: 'h-2 w-2',
  md: 'h-3 w-3',
  lg: 'h-4 w-4',
} as const;

const colorMap = {
  green: 'bg-neon-green',
  red: 'bg-neon-red',
  purple: 'bg-neon-purple',
  cyan: 'bg-neon-cyan',
  gold: 'bg-neon-gold',
} as const;

export function PulseIndicator({
  active,
  color = 'green',
  size = 'md',
}: PulseIndicatorProps) {
  return (
    <span className="relative inline-flex">
      {active && (
        <span
          className={cn(
            'absolute inline-flex h-full w-full rounded-full opacity-75 animate-pulse-dot',
            colorMap[color]
          )}
        />
      )}
      <span
        className={cn(
          'relative inline-flex rounded-full',
          sizeMap[size],
          active ? colorMap[color] : 'bg-text-muted'
        )}
      />
    </span>
  );
}
