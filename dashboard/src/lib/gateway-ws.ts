/**
 * Native WebSocket URLs for the nexus-api gateway (FastAPI), e.g. `/ws/ticks`.
 * Use NEXT_PUBLIC_WS_GATEWAY_URL as the origin only (no `/ws/...` suffix).
 */
export function gatewayWsUrl(path: string): string {
  const root = (
    process.env.NEXT_PUBLIC_WS_GATEWAY_URL || 'ws://127.0.0.1:9000'
  ).replace(/\/$/, '');
  const p = path.startsWith('/') ? path : `/${path}`;
  const withWs = p.startsWith('/ws') ? p : `/ws${p}`;
  return `${root}${withWs}`;
}
