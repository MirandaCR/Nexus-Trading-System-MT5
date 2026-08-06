'use client';

import { cn } from '@/lib/utils';
import { useEffect, useState } from 'react';

interface NeonGaugeProps {
  value: number;
  label?: string;
  size?: number;
  colorStops?: { offset: number; color: string }[];
  className?: string;
}

const DEFAULT_STOPS = [
  { offset: 0, color: 'var(--neon-green)' },
  { offset: 50, color: 'var(--neon-gold)' },
  { offset: 100, color: 'var(--neon-red)' },
];

export function NeonGauge({
  value,
  label,
  size = 120,
  colorStops = DEFAULT_STOPS,
  className,
}: NeonGaugeProps) {
  const [animatedValue, setAnimatedValue] = useState(0);
  const clamped = Math.max(0, Math.min(100, value));

  useEffect(() => {
    const timer = setTimeout(() => setAnimatedValue(clamped), 50);
    return () => clearTimeout(timer);
  }, [clamped]);

  const strokeWidth = size * 0.08;
  const radius = (size - strokeWidth) / 2;
  const circumference = Math.PI * radius; // semicircle
  const offset = circumference - (animatedValue / 100) * circumference;
  const center = size / 2;
  const gradientId = `gauge-gradient-${label?.replace(/\s/g, '') ?? 'default'}`;

  const glowColor =
    clamped < 33
      ? 'var(--neon-green)'
      : clamped < 66
        ? 'var(--neon-gold)'
        : 'var(--neon-red)';

  return (
    <div className={cn('flex flex-col items-center gap-1', className)}>
      <svg
        width={size}
        height={size * 0.6}
        viewBox={`0 0 ${size} ${size * 0.6}`}
        className="overflow-visible"
      >
        <defs>
          <linearGradient id={gradientId} x1="0%" y1="0%" x2="100%" y2="0%">
            {colorStops.map((stop, i) => (
              <stop
                key={i}
                offset={`${stop.offset}%`}
                stopColor={stop.color}
              />
            ))}
          </linearGradient>
          <filter id={`glow-${gradientId}`}>
            <feGaussianBlur stdDeviation="3" result="coloredBlur" />
            <feMerge>
              <feMergeNode in="coloredBlur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* Background arc */}
        <path
          d={`M ${strokeWidth / 2} ${center * 0.95} A ${radius} ${radius} 0 0 1 ${size - strokeWidth / 2} ${center * 0.95}`}
          fill="none"
          stroke="var(--border-primary)"
          strokeWidth={strokeWidth}
          strokeLinecap="round"
        />

        {/* Value arc */}
        <path
          d={`M ${strokeWidth / 2} ${center * 0.95} A ${radius} ${radius} 0 0 1 ${size - strokeWidth / 2} ${center * 0.95}`}
          fill="none"
          stroke={`url(#${gradientId})`}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          filter={`url(#glow-${gradientId})`}
          className="transition-all duration-700 ease-out"
        />

        {/* Center value */}
        <text
          x={center}
          y={center * 0.85}
          textAnchor="middle"
          dominantBaseline="middle"
          className="font-mono font-bold"
          fill={glowColor}
          fontSize={size * 0.22}
        >
          {Math.round(animatedValue)}
        </text>
      </svg>

      {label && (
        <span className="text-xs text-text-muted font-medium uppercase tracking-wider">
          {label}
        </span>
      )}
    </div>
  );
}
