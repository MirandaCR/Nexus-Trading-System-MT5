'use client';

import { type ReactNode } from 'react';

interface NeonCardProps {
  title?: string;
  icon?: ReactNode;
  glow?: 'green' | 'purple' | 'cyan' | 'red' | 'gold' | 'none';
  className?: string;
  headerRight?: ReactNode;
  noPadding?: boolean;
  /** Allow vertical scroll inside card body (default: clip, no scrollbar) */
  scrollable?: boolean;
  accentColor?: string;
  isLive?: boolean;
  pulseBorder?: boolean;
  borderColor?: string;
  children: ReactNode;
}

/** Colored top-accent bar instead of glow shadow */
const accentMap: Record<string, string> = {
  green:  'border-t-2 border-t-neon-green',
  purple: 'border-t-2 border-t-neon-purple',
  cyan:   'border-t-2 border-t-neon-cyan',
  red:    'border-t-2 border-t-neon-red',
  gold:   'border-t-2 border-t-neon-gold',
  none:   '',
};

export default function NeonCard({
  title,
  icon,
  glow = 'purple',
  className = '',
  headerRight,
  noPadding = false,
  scrollable = false,
  accentColor,
  isLive,
  pulseBorder,
  borderColor,
  children,
}: NeonCardProps) {
  return (
    <div
      className={`
        relative rounded-lg flex flex-col
        bg-bg-card border border-border-primary
        ${accentMap[glow]}
        ${borderColor ?? ''}
        ${className}
      `}
    >
      {title && (
        <div className="flex items-center justify-between border-b border-border-primary px-3 py-2 shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            {icon && <span className="text-text-secondary shrink-0">{icon}</span>}
            <h3 className="text-[11px] font-semibold tracking-wider text-text-secondary uppercase truncate">
              {title}
            </h3>
            {isLive && (
              <span
                className="h-1.5 w-1.5 rounded-full animate-pulse shrink-0"
                style={{ backgroundColor: accentColor ?? 'var(--neon-green)' }}
              />
            )}
          </div>
          {headerRight && <div className="shrink-0 ml-2">{headerRight}</div>}
        </div>
      )}
      <div
        className={
          noPadding
            ? `flex-1 flex flex-col min-h-0 ${scrollable ? 'overflow-auto' : 'overflow-hidden'}`
            : `p-3 flex-1 min-h-0 ${scrollable ? 'overflow-auto' : 'overflow-hidden'}`
        }
      >
        {children}
      </div>
    </div>
  );
}
