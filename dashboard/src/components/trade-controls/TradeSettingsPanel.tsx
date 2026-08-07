'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import { Sliders, RotateCcw, Save, Check, AlertTriangle } from 'lucide-react';

// Same-origin: /api/* is proxied to the nexus-api gateway by next.config.js
// rewrites. Relative URLs survive stale env bakes and never hit CORS.
const API = '';

/** fetch with a hard timeout so buttons can never hang in a loading state. */
function fetchT(url: string, init?: RequestInit, timeoutMs = 10_000): Promise<Response> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  return fetch(url, { ...init, signal: ctrl.signal }).finally(() => clearTimeout(timer));
}

interface Settings {
  risk_per_trade_pct: number;
  max_daily_loss_pct: number;
  max_open_positions: number;
  max_daily_trades: number;
  min_hold_minutes: number;
  max_hold_minutes: number;
  research_min_candidates: number;
  min_conviction_override: number | null;
  llm_provider?: string;
}

// AI provider options for the GenAI agents (live-switchable, no restart).
const LLM_PROVIDERS: Array<{ value: string; label: string }> = [
  { value: 'deepseek', label: 'DeepSeek (default)' },
  { value: 'openai', label: 'OpenAI · GPT' },
  { value: 'gemini', label: 'Google Gemini' },
  { value: 'local', label: 'Local · Ollama' },
];

interface SettingsResponse {
  settings: Settings;
  daily_trade_count?: number;
  trades_paused?: boolean;
  daily_loss_pct?: number;
}

// Editable numeric fields surfaced to the user.
const FIELDS: Array<{
  key: keyof Settings;
  label: string;
  hint: string;
  min: number;
  max: number;
  step: number;
}> = [
  { key: 'max_daily_loss_pct', label: 'Riesgo diario (%)', hint: 'Presupuesto de pérdida del día antes de pausar', min: 1, max: 50, step: 1 },
  { key: 'risk_per_trade_pct', label: 'Riesgo por trade (%)', hint: '% de equity arriesgado por operación', min: 0.1, max: 5, step: 0.1 },
  { key: 'max_daily_trades', label: 'Máx. trades / día', hint: 'Tope de operaciones diarias', min: 1, max: 100, step: 1 },
  { key: 'max_open_positions', label: 'Máx. posiciones', hint: 'Posiciones simultáneas abiertas', min: 1, max: 20, step: 1 },
  { key: 'min_hold_minutes', label: 'Hold mínimo (min)', hint: 'No salir antes de N minutos (scalping)', min: 0, max: 240, step: 1 },
  { key: 'research_min_candidates', label: 'Estrategias / ciclo', hint: 'Cuántas estrategias diseña la IA por ciclo', min: 1, max: 12, step: 1 },
];

export function TradeSettingsPanel() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [meta, setMeta] = useState<Omit<SettingsResponse, 'settings'>>({});
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetchT(`${API}/api/risk/settings`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: SettingsResponse = await res.json();
      setSettings(data.settings);
      setMeta({ daily_trade_count: data.daily_trade_count, trades_paused: data.trades_paused, daily_loss_pct: data.daily_loss_pct });
      setError(null);
    } catch (e) {
      setError('No se pudo cargar (gateway offline?)');
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, [load]);

  const setField = (key: keyof Settings, value: number) => {
    setSettings((prev) => (prev ? { ...prev, [key]: value } : prev));
    setDirty(true);
  };

  const save = async () => {
    if (!settings) return;
    setSaving(true);
    setError(null);
    try {
      const res = await fetchT(`${API}/api/risk/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data?.settings) setSettings(data.settings);
      setDirty(false);
      setSavedFlash(true);
      setTimeout(() => setSavedFlash(false), 1500);
    } catch (e) {
      setError('No se pudo guardar');
    } finally {
      setSaving(false);
    }
  };

  const resetDay = async () => {
    setResetting(true);
    setError(null);
    try {
      const res = await fetchT(`${API}/api/risk/reset-day`, { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await load();
    } catch (e) {
      setError('No se pudo resetear el día');
    } finally {
      setResetting(false);
    }
  };

  return (
    <div className="rounded-xl border border-border-primary/30 bg-bg-card overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-border-primary/20 bg-bg-secondary/40">
        <Sliders className="h-3.5 w-3.5 text-neon-purple" />
        <span className="text-xs font-semibold tracking-wider text-text-secondary uppercase">Trade Settings</span>
        <div className="flex-1" />
        {meta.daily_trade_count !== undefined && (
          <span className="text-[10px] font-mono text-text-muted">
            trades hoy: <span className="text-neon-cyan">{meta.daily_trade_count}</span>
            {settings ? ` / ${settings.max_daily_trades}` : ''}
          </span>
        )}
      </div>

      <div className="p-3 space-y-2.5">
        {error && (
          <div className="flex items-center gap-1.5 text-[10px] text-neon-red bg-neon-red/10 border border-neon-red/30 rounded px-2 py-1">
            <AlertTriangle className="h-3 w-3" /> {error}
          </div>
        )}

        {(meta.trades_paused || (meta.daily_trade_count !== undefined && settings && meta.daily_trade_count >= settings.max_daily_trades)) && (
          <div className="flex items-center justify-between gap-2 text-[10px] text-neon-gold bg-neon-gold/10 border border-neon-gold/30 rounded px-2 py-1.5">
            <span>
              {meta.trades_paused
                ? 'Trading en pausa'
                : `Límite diario ${meta.daily_trade_count}/${settings?.max_daily_trades}`}
              {' — '}
              <span className="text-neon-gold/80">pulsa Reset Día</span>
            </span>
            <button
              type="button"
              onClick={resetDay}
              disabled={resetting}
              className="shrink-0 flex items-center gap-1 rounded px-2 py-0.5 font-bold border bg-neon-gold/20 border-neon-gold/50 hover:bg-neon-gold/30 transition-all"
            >
              <RotateCcw className={cn('h-3 w-3', resetting && 'animate-spin')} />
              Reset
            </button>
          </div>
        )}

        {!settings && !error && (
          <div className="text-text-muted text-center py-4 text-xs">Cargando…</div>
        )}

        {settings && FIELDS.map((f) => (
          <div key={f.key} className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="text-[11px] text-text-secondary font-medium">{f.label}</div>
              <div className="text-[9px] text-text-muted truncate">{f.hint}</div>
            </div>
            <input
              type="number"
              min={f.min}
              max={f.max}
              step={f.step}
              value={Number(settings[f.key] ?? 0)}
              onChange={(e) => setField(f.key, parseFloat(e.target.value))}
              className="w-20 shrink-0 rounded-md bg-bg-input border border-border-primary px-2 py-1 text-right text-xs font-mono text-text-primary focus:border-neon-purple/50 focus:outline-none"
            />
          </div>
        ))}

        {settings && (
          <div className="flex items-center justify-between gap-3 pt-1 border-t border-border-primary/20">
            <div className="min-w-0">
              <div className="text-[11px] text-text-secondary font-medium">Proveedor de IA</div>
              <div className="text-[9px] text-text-muted truncate">Motor de los agentes GenAI (debate + árbitro). Cambio en vivo.</div>
            </div>
            <select
              value={settings.llm_provider ?? 'deepseek'}
              onChange={(e) => {
                setSettings((prev) => (prev ? { ...prev, llm_provider: e.target.value } : prev));
                setDirty(true);
              }}
              className="w-36 shrink-0 rounded-md bg-bg-input border border-border-primary px-2 py-1 text-right text-xs font-mono text-neon-cyan focus:border-neon-purple/50 focus:outline-none"
            >
              {LLM_PROVIDERS.map((p) => (
                <option key={p.value} value={p.value}>{p.label}</option>
              ))}
            </select>
          </div>
        )}

        <div className="flex items-center gap-2 pt-1">
          <button
            onClick={save}
            disabled={!dirty || saving}
            className={cn(
              'flex-1 flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-bold transition-all border',
              dirty
                ? 'bg-neon-green/15 border-neon-green/40 text-neon-green hover:bg-neon-green/25'
                : 'bg-bg-tertiary border-border-primary text-text-muted cursor-not-allowed'
            )}
          >
            {savedFlash ? <Check className="h-3.5 w-3.5" /> : <Save className="h-3.5 w-3.5" />}
            {savedFlash ? 'Guardado' : saving ? 'Guardando…' : 'Guardar'}
          </button>

          <button
            onClick={resetDay}
            disabled={resetting}
            title="Re-baseline del día: pérdida diaria a 0, reanuda trades aunque se haya pasado el riesgo"
            className="flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-bold border bg-neon-gold/10 border-neon-gold/40 text-neon-gold hover:bg-neon-gold/20 transition-all"
          >
            <RotateCcw className={cn('h-3.5 w-3.5', resetting && 'animate-spin')} />
            Reset Día
          </button>
        </div>

        {meta.daily_loss_pct !== undefined && (
          <div className="text-[10px] font-mono text-text-muted text-center">
            pérdida diaria actual: <span className={cn(meta.daily_loss_pct > 0 ? 'text-neon-red' : 'text-neon-green')}>{meta.daily_loss_pct.toFixed(2)}%</span>
          </div>
        )}
      </div>
    </div>
  );
}

export default TradeSettingsPanel;
