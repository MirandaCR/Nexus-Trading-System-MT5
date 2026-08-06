import { io, Socket } from 'socket.io-client';
import type { WSChannel, WSMessage, WSSubscription } from '@/types/api';

type EventCallback<T = unknown> = (data: WSMessage<T>) => void;

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:9000/ws';

const RECONNECT_BASE_DELAY = 1000;
const RECONNECT_MAX_DELAY = 30000;
const RECONNECT_MULTIPLIER = 2;

export type ConnectionStatus = 'connected' | 'connecting' | 'disconnected' | 'error';

class WSClient {
  private socket: Socket | null = null;
  private listeners = new Map<string, Set<EventCallback>>();
  private subscriptions = new Set<WSChannel>();
  private reconnectAttempt = 0;
  private statusCallbacks = new Set<(status: ConnectionStatus) => void>();
  private _status: ConnectionStatus = 'disconnected';

  get status(): ConnectionStatus {
    return this._status;
  }

  private setStatus(status: ConnectionStatus) {
    this._status = status;
    this.statusCallbacks.forEach((cb) => cb(status));
  }

  connect(): void {
    if (this.socket?.connected) return;

    this.setStatus('connecting');

    const token =
      typeof window !== 'undefined'
        ? localStorage.getItem('nexus-token')
        : null;

    this.socket = io(WS_URL, {
      transports: ['websocket', 'polling'],
      auth: token ? { token } : undefined,
      reconnection: false,
      timeout: 10000,
    });

    this.socket.on('connect', () => {
      this.reconnectAttempt = 0;
      this.setStatus('connected');
      this.resubscribeAll();
    });

    this.socket.on('disconnect', (reason) => {
      this.setStatus('disconnected');
      if (reason !== 'io client disconnect') {
        this.scheduleReconnect();
      }
    });

    this.socket.on('connect_error', () => {
      this.setStatus('error');
      this.scheduleReconnect();
    });

    this.socket.on('message', (msg: WSMessage) => {
      this.dispatch(msg.channel, msg);
      this.dispatch('*', msg);
    });
  }

  disconnect(): void {
    this.socket?.disconnect();
    this.socket = null;
    this.setStatus('disconnected');
  }

  private scheduleReconnect(): void {
    const delay = Math.min(
      RECONNECT_BASE_DELAY * Math.pow(RECONNECT_MULTIPLIER, this.reconnectAttempt),
      RECONNECT_MAX_DELAY
    );
    this.reconnectAttempt++;

    setTimeout(() => {
      if (this._status !== 'connected') {
        this.connect();
      }
    }, delay);
  }

  subscribe(channel: WSChannel, params?: Record<string, string | number | boolean>): void {
    this.subscriptions.add(channel);
    if (this.socket?.connected) {
      this.socket.emit('subscribe', { channel, params });
    }
  }

  unsubscribe(channel: WSChannel): void {
    this.subscriptions.delete(channel);
    if (this.socket?.connected) {
      this.socket.emit('unsubscribe', { channel });
    }
  }

  private resubscribeAll(): void {
    this.subscriptions.forEach((channel) => {
      this.socket?.emit('subscribe', { channel });
    });
  }

  on<T = unknown>(channel: WSChannel | '*', callback: EventCallback<T>): () => void {
    if (!this.listeners.has(channel)) {
      this.listeners.set(channel, new Set());
    }
    const set = this.listeners.get(channel)!;
    set.add(callback as EventCallback);

    return () => {
      set.delete(callback as EventCallback);
      if (set.size === 0) {
        this.listeners.delete(channel);
      }
    };
  }

  off(channel: WSChannel | '*', callback: EventCallback): void {
    this.listeners.get(channel)?.delete(callback);
  }

  private dispatch(channel: string, msg: WSMessage): void {
    this.listeners.get(channel)?.forEach((cb) => cb(msg));
  }

  onStatusChange(callback: (status: ConnectionStatus) => void): () => void {
    this.statusCallbacks.add(callback);
    callback(this._status);
    return () => {
      this.statusCallbacks.delete(callback);
    };
  }

  emit(event: string, data: unknown): void {
    this.socket?.emit(event, data);
  }
}

export const wsClient = new WSClient();
