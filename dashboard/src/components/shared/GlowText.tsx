'use client';

import { cn } from '@/lib/utils';
import type { ReactNode } from 'react';

interface GlowTextProps {
  children: ReactNode;
  color?: 'green' | 'red' | 'purple' | 'cyan' | 'gold';
  size?: 'xs' | 'sm' | 'md' | 'lg' | 'xl' | '2xl';
  pulse?: boolean;
  as?: 'span' | 'p' | 'h1' | 'h2' | 'h3' | 'h4';
  className?: string;
}

const colorClasses = {
  green: 'text-neon-green',
  red: 'text-neon-red',
  purple: 'text-neon-purple',
  cyan: 'text-neon-cyan',
  gold: 'text-neon-gold',
} as const;

const sizeClasses = {
  xs: 'text-xs',
  sm: 'text-sm',
  md: 'text-base',
  lg: 'text-lg',
  xl: 'text-xl',
  '2xl': 'text-2xl',
} as const;

export function GlowText({
  children,
  color = 'purple',
  size = 'md',
  pulse = false,
  as: Tag = 'span',
  className,
}: GlowTextProps) {
  return (
    <Tag
      className={cn(
        'font-semibold',
        colorClasses[color],
        sizeClasses[size],
        pulse && 'animate-neon-pulse',
        className
      )}
    >
      {children}
    </Tag>
  );
}
