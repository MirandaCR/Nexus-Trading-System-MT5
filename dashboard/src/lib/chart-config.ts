import type {
  ChartOptions,
  DeepPartial,
  CandlestickSeriesOptions,
  HistogramSeriesOptions,
  LineSeriesOptions,
} from 'lightweight-charts';

export const DARK_CHART_OPTIONS: DeepPartial<ChartOptions> = {
  layout: {
    background: { color: 'transparent' },
    textColor: '#9898b0',
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 11,
  },
  grid: {
    vertLines: { color: '#1a1a2e', style: 1 },
    horzLines: { color: '#1a1a2e', style: 1 },
  },
  crosshair: {
    mode: 0,
    vertLine: {
      color: '#5a5a78',
      width: 1,
      style: 2,
      labelBackgroundColor: '#2a2a3e',
    },
    horzLine: {
      color: '#5a5a78',
      width: 1,
      style: 2,
      labelBackgroundColor: '#2a2a3e',
    },
  },
  timeScale: {
    borderColor: '#2a2a3e',
    timeVisible: true,
    secondsVisible: false,
    rightOffset: 5,
    barSpacing: 8,
    minBarSpacing: 3,
  },
  rightPriceScale: {
    borderColor: '#2a2a3e',
    scaleMargins: {
      top: 0.1,
      bottom: 0.2,
    },
  },
  handleScroll: {
    mouseWheel: true,
    pressedMouseMove: true,
    horzTouchDrag: true,
    vertTouchDrag: false,
  },
  handleScale: {
    axisPressedMouseMove: true,
    mouseWheel: true,
    pinch: true,
  },
};

export const CANDLESTICK_OPTIONS: DeepPartial<CandlestickSeriesOptions> = {
  upColor: '#39ff14',
  downColor: '#ff3b5c',
  borderUpColor: '#39ff14',
  borderDownColor: '#ff3b5c',
  wickUpColor: '#39ff14',
  wickDownColor: '#ff3b5c',
};

export const VOLUME_OPTIONS: DeepPartial<HistogramSeriesOptions> = {
  color: 'rgba(0, 212, 255, 0.3)',
  priceFormat: {
    type: 'volume',
  },
  priceScaleId: 'volume',
};

export const VOLUME_SCALE_OPTIONS = {
  scaleMargins: {
    top: 0.8,
    bottom: 0,
  },
};

export const MA20_OPTIONS: DeepPartial<LineSeriesOptions> = {
  color: '#00d4ff',
  lineWidth: 1,
  priceLineVisible: false,
  lastValueVisible: false,
  crosshairMarkerVisible: false,
};

export const MA50_OPTIONS: DeepPartial<LineSeriesOptions> = {
  color: '#bf5af2',
  lineWidth: 1,
  priceLineVisible: false,
  lastValueVisible: false,
  crosshairMarkerVisible: false,
};

export const BB_UPPER_OPTIONS: DeepPartial<LineSeriesOptions> = {
  color: 'rgba(255, 215, 0, 0.4)',
  lineWidth: 1,
  lineStyle: 2,
  priceLineVisible: false,
  lastValueVisible: false,
  crosshairMarkerVisible: false,
};

export const BB_LOWER_OPTIONS: DeepPartial<LineSeriesOptions> = {
  ...BB_UPPER_OPTIONS,
};

export const SIGNAL_MARKER_COLORS = {
  buy: '#39ff14',
  sell: '#ff3b5c',
  stopLoss: '#ff9500',
  takeProfit: '#00d4ff',
} as const;

export function createChartOptions(
  overrides?: DeepPartial<ChartOptions>
): DeepPartial<ChartOptions> {
  return {
    ...DARK_CHART_OPTIONS,
    ...overrides,
  };
}
