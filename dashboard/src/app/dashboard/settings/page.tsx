'use client';

import React, { useEffect, useState, useCallback } from 'react';
import {
  Settings, Save, RotateCcw, Cpu, Shield, Zap, Brain, Bot,
  ChevronDown, ChevronRight, AlertTriangle, Globe, Database, Bell, FlaskConical,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { apiClient } from '@/lib/api-client';
import { UI_SETTINGS_STORAGE_KEY } from '@/lib/ui-settings';
import { useDashboardStore } from '@/stores/dashboardStore';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9000';

// ── Section schema ─────────────────────────────────────────────────────────────
interface SettingField {
  key: string;
  label: string;
  description: string;
  type: 'number' | 'boolean' | 'select' | 'text';
  min?: number;
  max?: number;
  step?: number;
  options?: string[];
  unit?: string;
}

interface SettingSection {
  id: string;
  label: string;
  icon: React.ElementType;
  color: string;
  fields: SettingField[];
}

const RUNTIME_SECTIONS: SettingSection[] = [
  {
    id: 'trading',
    label: 'Trading Core',
    icon: Zap,
    color: 'text-neon-green',
    fields: [
      { key: 'trading_mode_type',     label: 'Modo operativa',        type: 'select',  options: ['scalping', 'swing'], description: 'Modo activo de trading' },
      { key: 'auto_tf_selection',     label: 'TF Automático',         type: 'boolean', description: 'Selección dinámica del timeframe operacional según sesión' },
      { key: 'operational_tf',        label: 'Timeframe operacional', type: 'select',  options: ['M1', 'M5', 'M15', 'H1'], description: 'TF de ejecución (override si auto_tf=true)' },
      { key: 'require_mtf_confirmation', label: 'Confirmación MTF',   type: 'boolean', description: 'Exige alineación de TFs superiores antes de entrar' },
      { key: 'min_mtf_agreement',     label: 'Min TFs de acuerdo',    type: 'number',  min: 0, max: 4, step: 1, description: 'Cuántos TFs superiores deben coincidir' },
      { key: 'max_daily_trades',      label: 'Máx trades / día',      type: 'number',  min: 1, max: 50, step: 1, description: 'Límite de operaciones por día y símbolo' },
      { key: 'allow_off_hours',       label: 'Fuera de sesión',       type: 'boolean', description: 'Permitir trades fuera de Tokio/Londres/NY' },
    ],
  },
  {
    id: 'risk',
    label: 'Risk & Sizing',
    icon: Shield,
    color: 'text-neon-red',
    fields: [
      { key: 'risk_per_trade',        label: 'Riesgo / trade',        type: 'number', min: 0.001, max: 0.05, step: 0.001, unit: '%', description: 'Fracción del balance arriesgada por operación' },
      { key: 'max_spread_pips',       label: 'Spread máximo',         type: 'number', min: 0.1, max: 10, step: 0.1, unit: 'pips', description: 'Rechaza entrada si spread supera este valor' },
      { key: 'sl_atr_multiplier',     label: 'SL × ATR',              type: 'number', min: 1.0, max: 5.0, step: 0.1, description: 'Stop-loss mínimo en múltiplos de ATR' },
      { key: 'tp_atr_multiplier',     label: 'TP × ATR',              type: 'number', min: 2.0, max: 10.0, step: 0.5, description: 'Take-profit objetivo en múltiplos de ATR' },
      { key: 'min_rr_ratio',          label: 'RR mínimo',             type: 'number', min: 1.0, max: 5.0, step: 0.1, description: 'Relación Risk/Reward mínima aceptable' },
      { key: 'max_hold_bars',         label: 'Máx bars en trade',     type: 'number', min: 1, max: 48, step: 1, unit: 'bars', description: 'Cierra por tiempo si se supera este límite' },
      { key: 'cooling_off_streak',    label: 'Streak para pausa',     type: 'number', min: 2, max: 10, step: 1, unit: 'perdidas', description: 'Pérdidas consecutivas para entrar en pausa' },
      { key: 'sl_floor_atr_mult',     label: 'SL floor × ATR',        type: 'number', min: 1.0, max: 5.0, step: 0.1, description: 'Mínimo absoluto del SL como múltiplo de ATR (default 2.5). Evita SLs muy ajustados.' },
      { key: 'entry_displacement_atr_mult', label: 'Entrada max despl. ATR', type: 'number', min: 0.5, max: 5.0, step: 0.1, description: 'Rechaza entrada si el precio ya se desplazó N×ATR en la dirección de la señal (default 2.0).' },
    ],
  },
  {
    id: 'signals',
    label: 'Señales & Calidad',
    icon: Cpu,
    color: 'text-neon-purple',
    fields: [
      { key: 'quality_score_threshold', label: 'Quality score override', type: 'number', min: 0, max: 90, step: 5, description: 'Score mínimo manual. 0 = usa dinámico de TradeMemory (0-100)' },
      { key: 'min_conviction_override', label: 'Convicción override',    type: 'number', min: 0, max: 0.90, step: 0.01, description: '0 = usa dinámico por sesión. Valor fijo si > 0' },
      { key: 'autonomous_ml',          label: 'Pipeline ML autónomo',   type: 'boolean', description: 'Ejecuta ML/DL/RL sin esperar confirmación GenAI' },
      { key: 'pre_signal_spread_filter', label: 'Filtro spread pre-señal', type: 'boolean', description: 'Bloquea señales cuando el spread está elevado' },
      { key: 'signal_cooldown_sec',    label: 'Cooldown entre señales', type: 'number', min: 10, max: 300, step: 10, unit: 's', description: 'Segundos mínimos entre señales del mismo símbolo' },
      { key: 'min_confidence_floor',   label: 'Piso convicción mínima', type: 'number', min: 0.20, max: 0.70, step: 0.01, description: 'Convicción mínima absoluta post-ajuste de TradeMemory (default 0.40)' },
      { key: 'conviction_min',         label: 'Convicción base',        type: 'number', min: 0.40, max: 0.80, step: 0.01, description: 'Convicción mínima base fuera de sesión configurada (default 0.55)' },
    ],
  },
  {
    id: 'models',
    label: 'Modelos & Entrenamiento',
    icon: Brain,
    color: 'text-neon-cyan',
    fields: [
      { key: 'retrain_interval_hours', label: 'Retrain cada',          type: 'number', min: 1, max: 24, step: 1, unit: 'h', description: 'Frecuencia de re-entrenamiento automático de todos los modelos' },
      { key: 'outcome_weight',        label: 'Peso trades reales',     type: 'number', min: 1, max: 10, step: 1, unit: '×', description: 'Multiplicador para records de trades reales en entrenamiento' },
      { key: 'allow_synthetic_training', label: 'Datos sintéticos',    type: 'boolean', description: 'Usar datos sintéticos solo si no hay reales disponibles' },
      { key: 'dl_seq_len',            label: 'DL ventana temporal',    type: 'number', min: 5, max: 50, step: 5, unit: 'bars', description: 'Longitud de la secuencia de entrada del Transformer' },
      { key: 'dl_confidence_threshold', label: 'DL umbral confianza',  type: 'number', min: 0.30, max: 0.80, step: 0.05, description: 'Confianza mínima del DL para emitir señal válida' },
      { key: 'rl_n_epochs',           label: 'RL epochs / ciclo',      type: 'number', min: 5, max: 50, step: 5, description: 'Epochs de entrenamiento PPO por ciclo de re-entrenamiento' },
    ],
  },
  {
    id: 'notifications',
    label: 'Notificaciones',
    icon: Bell,
    color: 'text-neon-purple',
    fields: [
      { key: 'notif_enable_browser',   label: 'Notif. navegador',     type: 'boolean', description: 'Mostrar notificaciones push del sistema operativo cuando la pestaña no está activa' },
      { key: 'notif_blackout',         label: 'Alertas blackout',     type: 'boolean', description: 'Notificar cuando se activa o desactiva un blackout de noticias de alto impacto' },
      { key: 'notif_brief',            label: 'Brief generado',       type: 'boolean', description: 'Notificar cuando el agente GenAI genera un nuevo brief macro diario' },
      { key: 'notif_corr_divergence',  label: 'Divergencia correlación', type: 'boolean', description: 'Notificar cuando se detecta una divergencia HIGH en la matriz de correlación multi-activo' },
    ],
  },
  {
    id: 'correlation',
    label: 'Correlación Multi-Activo',
    icon: Globe,
    color: 'text-neon-purple',
    fields: [
      { key: 'corr_refresh_sec',           label: 'Refresh correlación',    type: 'number', min: 60, max: 3600, step: 60, unit: 's', description: 'Cada cuántos segundos recalcular la matriz de correlación (default 300 = 5 min)' },
      { key: 'corr_divergence_threshold',  label: 'Umbral divergencia',     type: 'number', min: 0.10, max: 0.70, step: 0.05, description: 'Cambio mínimo 7d-vs-30d para marcar divergencia (default 0.25)' },
      { key: 'corr_window_default',        label: 'Ventana por defecto',    type: 'select', options: ['7d', '30d'], description: 'Ventana temporal mostrada al abrir el panel de correlación' },
    ],
  },
  {
    id: 'trade_memory',
    label: 'Trade Memory',
    icon: Brain,
    color: 'text-neon-purple',
    fields: [
      { key: 'trade_memory_window',       label: 'Ventana de memoria',         type: 'number', min: 20, max: 500, step: 10, unit: 'trades', description: 'Número de trades recientes que el sistema recuerda para calcular thresholds dinámicos (default 100)' },
      { key: 'cooling_off_duration_sec',  label: 'Duración cooling-off',       type: 'number', min: 60, max: 3600, step: 60, unit: 's', description: 'Segundos de pausa tras alcanzar el streak de pérdidas consecutivas (default 300 = 5 min)' },
    ],
  },
  {
    id: 'backtest',
    label: 'Backtesting',
    icon: FlaskConical,
    color: 'text-neon-cyan',
    fields: [
      { key: 'backtest_initial_equity',   label: 'Equity inicial',             type: 'number', min: 500, max: 500000, step: 500, unit: '$', description: 'Capital inicial usado por defecto al abrir el panel de Backtest' },
      { key: 'backtest_default_symbol',   label: 'Símbolo por defecto',        type: 'select', options: ['XAUUSD', 'EURUSD', 'BTCUSD', 'US500'], description: 'Símbolo pre-seleccionado al abrir el panel de Backtest' },
    ],
  },
  {
    id: 'genai',
    label: 'GenAI Agentes',
    icon: Bot,
    color: 'text-yellow-400',
    fields: [
      { key: 'disable_research',      label: 'Pausar research',        type: 'boolean', description: 'Pausa el research semanal de estrategias' },
      { key: 'research_interval_sec', label: 'Intervalo research',     type: 'number', min: 900, max: 86400, step: 900, unit: 's', description: 'Frecuencia del ciclo de investigación de estrategias' },
      { key: 'strategy_min_profit_factor', label: 'PF mínimo',        type: 'number', min: 1.0, max: 3.0, step: 0.1, description: 'PF mínimo para promover una estrategia nueva' },
      { key: 'strategy_min_win_rate', label: 'WR mínimo',             type: 'number', min: 0.40, max: 0.80, step: 0.01, unit: '%', description: 'Win Rate mínimo para adoptar nueva estrategia' },
      { key: 'genai_max_calls_per_hour', label: 'Calls GenAI / hora', type: 'number', min: 5, max: 120, step: 5, description: 'Límite de llamadas a la API GenAI por hora' },
    ],
  },
];

// ── Local UI settings (stored in localStorage, also synced to gateway) ─────────
const DEFAULT_LOCAL = {
  paperMode: true,
  autoTrade: false,
  soundAlerts: true,
  desktopNotifications: false,
};

type LocalSettings = typeof DEFAULT_LOCAL;

// ── Main Component ─────────────────────────────────────────────────────────────

export default function SettingsPage() {
  // Runtime settings (from Redis via API)
  const [runtime, setRuntime] = useState<Record<string, any>>({});
  const [runtimeOrig, setRuntimeOrig] = useState<Record<string, any>>({});

  // Local UI settings
  const [local, setLocal] = useState<LocalSettings>(() => {
    if (typeof window === 'undefined') return DEFAULT_LOCAL;
    try {
      const raw = localStorage.getItem(UI_SETTINGS_STORAGE_KEY);
      if (raw) return { ...DEFAULT_LOCAL, ...JSON.parse(raw) };
    } catch {}
    return DEFAULT_LOCAL;
  });

  // Side panel data
  const [dynamicThresholds, setDynamicThresholds] = useState<any>(null);
  const [mt5Connected, setMt5Connected] = useState(false);
  const [wsConnected, setWsConnected] = useState(false);

  // UI state
  const [expanded, setExpanded] = useState<Record<string, boolean>>({ trading: true, risk: true });
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    try {
      const [settRes, tRes, healthRes, posRes] = await Promise.all([
        fetch(`${API}/api/intelligence/settings`),
        fetch(`${API}/api/regime/thresholds`),
        apiClient.getHealth().catch(() => null),
        apiClient.getPositionsSnapshot().catch(() => null),
      ]);
      if (settRes.ok) {
        const d = await settRes.json();
        setRuntime(d.settings || {});
        setRuntimeOrig(d.settings || {});
      }
      if (tRes.ok) setDynamicThresholds(await tRes.json());
      if (healthRes?.data) {
        const redisOk = healthRes.data.components?.some((c: any) => c.name === 'redis' && c.status === 'healthy');
        setWsConnected(Boolean(redisOk));
      }
      if (posRes?.data) setMt5Connected(Boolean(posRes.data.live_connected));
    } catch {}
  }, []);

  useEffect(() => {
    const st = useDashboardStore.getState();
    setLocal((p) => ({ ...p, paperMode: st.mode === 'PAPER', autoTrade: st.autoTradeEnabled }));
    loadAll();
    const t = setInterval(loadAll, 30000);
    return () => clearInterval(t);
  }, [loadAll]);

  const setRuntimeField = (key: string, value: any) => setRuntime((p) => ({ ...p, [key]: value }));
  const setLocalField = <K extends keyof LocalSettings>(key: K, value: LocalSettings[K]) =>
    setLocal((p) => ({ ...p, [key]: value }));

  const runtimeChanged = JSON.stringify(runtime) !== JSON.stringify(runtimeOrig);
  const localChanged = JSON.stringify(local) !== JSON.stringify(DEFAULT_LOCAL);

  const save = async () => {
    setSaving(true);
    const messages: string[] = [];
    try {
      // Save runtime settings to Redis
      const res = await fetch(`${API}/api/intelligence/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ settings: runtime }),
      });
      if (res.ok) {
        setRuntimeOrig({ ...runtime });
        messages.push('runtime synced');
      } else {
        messages.push('runtime error');
      }
    } catch { messages.push('runtime offline'); }

    try {
      // Save local settings
      localStorage.setItem(UI_SETTINGS_STORAGE_KEY, JSON.stringify(local));
      const store = useDashboardStore.getState();
      store.setAutoTrade(local.autoTrade);
      store.setMode(local.paperMode ? 'PAPER' : 'LIVE');
      await apiClient.putGatewaySettings({
        trading_mode: local.paperMode ? 'paper' : 'live',
        auto_trade_enabled: local.autoTrade,
        default_symbol: store.symbol,
        default_timeframe: store.timeframe,
      });
      messages.push('gateway synced');
    } catch { messages.push('gateway offline'); }

    setSaveMsg(messages.join(' · '));
    setSaving(false);
    setTimeout(() => setSaveMsg(null), 5000);
  };

  const reset = () => {
    setRuntime({ ...runtimeOrig });
  };

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      {/* Page header */}
      <div className="sticky top-0 z-10 flex items-center gap-3 px-4 py-3 bg-bg-secondary/95 backdrop-blur border-b border-border-primary">
        <Settings className="h-4 w-4 text-neon-purple" />
        <span className="text-[13px] font-mono font-bold text-text-primary tracking-wider">PANEL DE CONFIGURACIÓN</span>
        <span className="text-[9px] font-mono text-text-muted ml-1">sin hardcode · todo configurable</span>
        <div className="flex-1" />
        {saveMsg && (
          <span className="text-[10px] font-mono text-neon-cyan">{saveMsg}</span>
        )}
        {runtimeChanged && (
          <button onClick={reset} className="flex items-center gap-1.5 px-3 py-1 rounded text-[10px] font-mono text-text-muted hover:text-text-secondary border border-border-primary transition-all">
            <RotateCcw className="h-3 w-3" />
            Revertir
          </button>
        )}
        <button
          onClick={save}
          disabled={(!runtimeChanged && !localChanged) || saving}
          className={cn(
            'flex items-center gap-1.5 px-4 py-1.5 rounded text-[11px] font-mono font-bold border transition-all',
            (runtimeChanged || localChanged) && !saving
              ? 'bg-neon-purple/20 text-neon-purple border-neon-purple/40 hover:bg-neon-purple/30'
              : 'text-text-muted border-border-primary cursor-not-allowed'
          )}
        >
          {saving
            ? <><span className="animate-pulse">●</span> Guardando...</>
            : <><Save className="h-3 w-3" /> Guardar todo</>
          }
        </button>
      </div>

      <div className="flex gap-4 p-4 max-w-[1400px] mx-auto w-full">
        {/* Left: runtime + local sections */}
        <div className="flex-1 min-w-0 flex flex-col gap-3">

          {/* ── Local section (paper mode / auto trade) ── */}
          <SectionCard
            id="local" label="Operación" icon={Globe} color="text-text-secondary"
            expanded={expanded} setExpanded={setExpanded}
          >
            <FieldRow
              field={{ key: 'paperMode', label: 'Paper Mode', type: 'boolean', description: 'Opera con dinero simulado. Desactiva para usar cuenta MT5 real.' }}
              value={local.paperMode}
              onChange={(v) => setLocalField('paperMode', v)}
            />
            <FieldRow
              field={{ key: 'autoTrade', label: 'Auto-Trade', type: 'boolean', description: 'Permite a los agentes ejecutar trades automáticamente en MT5.' }}
              value={local.autoTrade}
              onChange={(v) => setLocalField('autoTrade', v)}
            />
            <FieldRow
              field={{ key: 'soundAlerts', label: 'Alertas de sonido', type: 'boolean', description: 'Sonido en ejecución de trades y señales.' }}
              value={local.soundAlerts}
              onChange={(v) => setLocalField('soundAlerts', v)}
            />
          </SectionCard>

          {/* ── Runtime sections from Redis ── */}
          {RUNTIME_SECTIONS.map((section) => (
            <SectionCard
              key={section.id} id={section.id} label={section.label}
              icon={section.icon} color={section.color}
              expanded={expanded} setExpanded={setExpanded}
            >
              {section.fields.map((f) => (
                <FieldRow key={f.key} field={f} value={runtime[f.key]} onChange={(v) => setRuntimeField(f.key, v)} />
              ))}
            </SectionCard>
          ))}

          {/* ── Connection info ── */}
          <SectionCard
            id="connection" label="Conexión" icon={Database} color="text-text-muted"
            expanded={expanded} setExpanded={setExpanded}
          >
            <div className="flex items-center justify-between py-1.5">
              <span className="text-[10px] font-mono text-text-secondary">MetaTrader 5</span>
              <StatusPill ok={mt5Connected} />
            </div>
            <div className="flex items-center justify-between py-1.5">
              <span className="text-[10px] font-mono text-text-secondary">Redis / WebSocket</span>
              <StatusPill ok={wsConnected} />
            </div>
            <div className="flex items-center justify-between py-1.5">
              <span className="text-[10px] font-mono text-text-secondary">API Endpoint</span>
              <span className="text-[9px] font-mono text-text-muted">{API}</span>
            </div>
            <div className="flex items-center justify-between py-1.5">
              <span className="text-[10px] font-mono text-text-secondary">DeepSeek API</span>
              <span className="text-[9px] font-mono text-text-muted">env: DEEPSEEK_API_KEY</span>
            </div>
          </SectionCard>
        </div>

        {/* Right: dynamic thresholds panel */}
        <div className="w-72 shrink-0 flex flex-col gap-3">
          <div className="border border-neon-purple/20 rounded-lg p-3 bg-neon-purple/5">
            <div className="text-[9px] font-mono text-neon-purple mb-2 tracking-wider font-bold">THRESHOLDS DINÁMICOS (TradeMemory)</div>
            {dynamicThresholds ? (
              <div className="flex flex-col gap-1.5">
                <ThreshRow label="Quality score min" value={`${dynamicThresholds.quality_score_threshold ?? '--'}/100`} />
                <ThreshRow label="Convicción min" value={`${((dynamicThresholds.conviction_min ?? 0) * 100).toFixed(0)}%`} />
                <ThreshRow
                  label="Win Rate general"
                  value={`${((dynamicThresholds.overall_wr ?? 0) * 100).toFixed(1)}%`}
                  color={dynamicThresholds.overall_wr >= 0.5 ? 'text-neon-green' : dynamicThresholds.overall_wr >= 0.4 ? 'text-yellow-400' : 'text-neon-red'}
                />
                <ThreshRow label="Trades base" value={String(dynamicThresholds.total_trades ?? 0)} />
              </div>
            ) : (
              <div className="text-[9px] font-mono text-text-muted">Cargando...</div>
            )}
            <div className="mt-2 text-[8px] font-mono text-text-muted">
              Auto-calculados desde resultados reales. Se actualizan tras cada trade.
            </div>
          </div>

          {dynamicThresholds?.bad_hours?.length > 0 && (
            <div className="border border-neon-red/20 rounded-lg p-3 bg-neon-red/5">
              <div className="text-[9px] font-mono text-neon-red mb-1.5 tracking-wider font-bold">HORAS MALAS (UTC)</div>
              <div className="flex flex-wrap gap-1">
                {dynamicThresholds.bad_hours.map((h: string) => (
                  <span key={h} className="text-[8px] font-mono text-neon-red bg-neon-red/10 px-1.5 py-0.5 rounded">{h}</span>
                ))}
              </div>
            </div>
          )}

          {dynamicThresholds?.good_hours?.length > 0 && (
            <div className="border border-neon-green/20 rounded-lg p-3 bg-neon-green/5">
              <div className="text-[9px] font-mono text-neon-green mb-1.5 tracking-wider font-bold">HORAS BUENAS (UTC)</div>
              <div className="flex flex-wrap gap-1">
                {dynamicThresholds.good_hours.map((h: string) => (
                  <span key={h} className="text-[8px] font-mono text-neon-green bg-neon-green/10 px-1.5 py-0.5 rounded">{h}</span>
                ))}
              </div>
            </div>
          )}

          {dynamicThresholds?.regime_min_wr && Object.keys(dynamicThresholds.regime_min_wr).length > 0 && (
            <div className="border border-border-primary rounded-lg p-3">
              <div className="text-[9px] font-mono text-text-muted mb-2 tracking-wider">WR POR RÉGIMEN</div>
              {Object.entries(dynamicThresholds.regime_min_wr as Record<string, number>)
                .sort((a, b) => b[1] - a[1])
                .map(([regime, wr]) => (
                  <div key={regime} className="flex items-center gap-2 mb-1">
                    <span className="text-[9px] font-mono text-text-muted flex-1 truncate">{regime}</span>
                    <span className={cn('text-[9px] font-mono font-bold',
                      wr >= 0.5 ? 'text-neon-green' : wr >= 0.35 ? 'text-yellow-400' : 'text-neon-red'
                    )}>{wr > 0 ? `${(wr * 100).toFixed(0)}%` : 'sin datos'}</span>
                  </div>
                ))}
            </div>
          )}

          <div className="border border-yellow-500/20 rounded-lg p-3 bg-yellow-500/5">
            <div className="flex items-center gap-1.5 mb-1.5">
              <AlertTriangle className="h-3 w-3 text-yellow-400" />
              <span className="text-[9px] font-mono text-yellow-400 font-bold">OVERRIDES MANUALES</span>
            </div>
            <p className="text-[9px] font-mono text-text-muted leading-relaxed">
              Los overrides (quality_score_threshold, min_conviction_override) solo aplican si son {'>'} 0.
              Valor 0 = usa dinámico de TradeMemory.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function SectionCard({
  id, label, icon: Icon, color, expanded, setExpanded, children,
}: {
  id: string; label: string; icon: React.ElementType; color: string;
  expanded: Record<string, boolean>; setExpanded: React.Dispatch<React.SetStateAction<Record<string, boolean>>>;
  children: React.ReactNode;
}) {
  const isExp = expanded[id] !== false;
  return (
    <div className="border border-border-primary rounded-lg overflow-hidden">
      <button
        onClick={() => setExpanded((p) => ({ ...p, [id]: !isExp }))}
        className="w-full flex items-center gap-2.5 px-4 py-2.5 bg-bg-secondary/60 hover:bg-bg-secondary transition-colors"
      >
        <Icon className={cn('h-3.5 w-3.5', color)} />
        <span className="text-[11px] font-mono font-bold text-text-secondary">{label.toUpperCase()}</span>
        {isExp
          ? <ChevronDown className="h-3 w-3 text-text-muted ml-auto" />
          : <ChevronRight className="h-3 w-3 text-text-muted ml-auto" />
        }
      </button>
      {isExp && (
        <div className="px-4 py-3 flex flex-col gap-3">
          {children}
        </div>
      )}
    </div>
  );
}

function FieldRow({ field, value, onChange }: { field: SettingField; value: any; onChange: (v: any) => void }) {
  return (
    <div className="flex items-start gap-4">
      <div className="flex-1 min-w-0">
        <div className="text-[10px] font-mono font-bold text-text-secondary">{field.label}</div>
        <div className="text-[9px] font-mono text-text-muted mt-0.5 leading-relaxed">{field.description}</div>
      </div>
      <div className="shrink-0 w-36">
        {field.type === 'boolean' ? (
          <button
            onClick={() => onChange(!value)}
            className={cn(
              'flex items-center gap-2 px-2.5 py-1 rounded border text-[10px] font-mono font-bold transition-all w-full justify-center',
              value
                ? 'bg-neon-green/10 text-neon-green border-neon-green/30'
                : 'bg-bg-tertiary text-text-muted border-border-primary hover:border-border-secondary'
            )}
          >
            <div className={cn('w-2 h-2 rounded-full transition-colors', value ? 'bg-neon-green' : 'bg-text-muted')} />
            {value ? 'ON' : 'OFF'}
          </button>
        ) : field.type === 'select' ? (
          <select
            value={value ?? ''}
            onChange={(e) => onChange(e.target.value)}
            className="w-full bg-bg-tertiary border border-border-primary text-text-secondary text-[10px] font-mono px-2 py-1.5 rounded focus:outline-none focus:border-neon-purple/40 cursor-pointer"
          >
            {field.options?.map((opt) => (
              <option key={opt} value={opt}>{opt}</option>
            ))}
          </select>
        ) : (
          <div className="flex items-center gap-1.5">
            <input
              type="number"
              value={value ?? ''}
              min={field.min}
              max={field.max}
              step={field.step}
              onChange={(e) => onChange(parseFloat(e.target.value))}
              className="flex-1 min-w-0 bg-bg-tertiary border border-border-primary text-text-secondary text-[10px] font-mono px-2 py-1.5 rounded focus:outline-none focus:border-neon-purple/40"
            />
            {field.unit && <span className="text-[9px] font-mono text-text-muted shrink-0">{field.unit}</span>}
          </div>
        )}
      </div>
    </div>
  );
}

function ThreshRow({ label, value, color = 'text-text-secondary' }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-[9px] font-mono text-text-muted">{label}</span>
      <span className={cn('text-[9px] font-mono font-bold', color)}>{value}</span>
    </div>
  );
}

function StatusPill({ ok }: { ok: boolean }) {
  return (
    <span className={cn(
      'text-[9px] font-mono px-2 py-0.5 rounded',
      ok ? 'bg-neon-green/10 text-neon-green' : 'bg-neon-red/10 text-neon-red'
    )}>
      {ok ? '● Connected' : '○ Offline'}
    </span>
  );
}
