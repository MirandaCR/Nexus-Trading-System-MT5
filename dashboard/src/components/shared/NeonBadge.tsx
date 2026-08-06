'use client';

import { cn } from '@/lib/utils';
import type { NeonVariant } from '@/types';

interface NeonBadgeProps {
  label: string;
  variant?: NeonVariant;
  size?: 'sm' | 'md';
  pulse?: boolean;
  className?: string;
}

const variantStyles: Record<NeonVariant, string> = {
  success: 'text-neon-green border-neon-green/30 bg-neon-green/8',
  danger:  'text-neon-red border-neon-red/30 bg-neon-red/8',
  warning: 'text-neon-gold border-neon-gold/30 bg-neon-gold/8',
  info:    'text-neon-cyan border-neon-cyan/30 bg-neon-cyan/8',
  neutral: 'text-text-secondary border-border-primary bg-bg-tertiary',
};

export function NeonBadge({
  label,
  variant = 'neutral',
  size = 'sm',
  pulse = false,
  className,
}: NeonBadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-md border font-mono font-medium uppercase tracking-wider',
        size === 'sm' ? 'px-2 py-0.5 text-[10px]' : 'px-3 py-1 text-xs',
        variantStyles[variant],
        pulse && 'animate-neon-pulse',
        className
      )}
    >
      {label}
    </span>
  );
}
