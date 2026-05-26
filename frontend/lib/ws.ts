'use client';

// Real-time client for /ws/unit/{unitId}. Exponential backoff reconnection.

export type UnitWsEvent =
  | { type: 'bed_updated'; payload: unknown }
  | { type: 'counter_updated'; payload: unknown }
  | { type: 'specialist_updated'; payload: unknown }
  | { type: 'exam_updated'; payload: unknown }
  | { type: 'unit_snapshot'; payload: unknown }
  | { type: string; payload: unknown };

export type UnitWsStatus = 'connecting' | 'open' | 'closed';

export interface UnitWebSocketOptions {
  unitId?: string;
  onEvent?: (event: UnitWsEvent) => void;
  onStatusChange?: (status: UnitWsStatus) => void;
  onReopen?: () => void;
}

const BACKOFF_STEPS_MS = [1000, 2000, 4000, 8000, 16000, 30000];

export class UnitWebSocket {
  readonly unitId: string;
  onEvent?: (event: UnitWsEvent) => void;
  onStatusChange?: (status: UnitWsStatus) => void;
  onReopen?: () => void;

  private ws: WebSocket | null = null;
  private attempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private explicitlyClosed = false;
  private hasOpenedOnce = false;

  constructor(
    unitIdOrOpts: string | UnitWebSocketOptions,
    onEvent?: (event: UnitWsEvent) => void,
  ) {
    if (typeof unitIdOrOpts === 'string') {
      this.unitId = unitIdOrOpts;
      this.onEvent = onEvent;
    } else {
      this.unitId = unitIdOrOpts.unitId ?? '';
      this.onEvent = unitIdOrOpts.onEvent;
      this.onStatusChange = unitIdOrOpts.onStatusChange;
      this.onReopen = unitIdOrOpts.onReopen;
    }
    if (this.unitId) {
      // Auto-connect when constructed with a unitId.
      this.connect();
    }
  }

  private buildUrl(): string {
    const proto = typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'wss' : 'ws';
    const host = typeof window !== 'undefined' ? window.location.host : 'localhost:3000';
    return `${proto}://${host}/ws/unit/${this.unitId}`;
  }

  connect(): void {
    if (typeof window === 'undefined') return;
    if (!this.unitId) return;
    this.explicitlyClosed = false;

    this.cleanupSocket();
    this.onStatusChange?.('connecting');

    let socket: WebSocket;
    try {
      socket = new WebSocket(this.buildUrl());
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = socket;

    socket.onopen = () => {
      this.attempt = 0;
      this.onStatusChange?.('open');
      if (this.hasOpenedOnce) {
        this.onReopen?.();
      }
      this.hasOpenedOnce = true;
    };

    socket.onmessage = (msg) => {
      try {
        const parsed = JSON.parse(msg.data);
        if (parsed && typeof parsed === 'object' && 'type' in parsed) {
          this.onEvent?.(parsed as UnitWsEvent);
        }
      } catch {
        // ignore non-JSON frames
      }
    };

    socket.onerror = () => {
      // close handler will run reconnect.
    };

    socket.onclose = () => {
      this.ws = null;
      this.onStatusChange?.('closed');
      if (!this.explicitlyClosed) {
        this.scheduleReconnect();
      }
    };
  }

  private scheduleReconnect(): void {
    if (this.explicitlyClosed) return;
    const delay = BACKOFF_STEPS_MS[Math.min(this.attempt, BACKOFF_STEPS_MS.length - 1)];
    this.attempt += 1;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  private cleanupSocket(): void {
    if (this.ws) {
      try {
        this.ws.onopen = null;
        this.ws.onmessage = null;
        this.ws.onerror = null;
        this.ws.onclose = null;
        if (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING) {
          this.ws.close();
        }
      } catch {
        // ignore
      }
      this.ws = null;
    }
  }

  close(): void {
    this.explicitlyClosed = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.cleanupSocket();
    this.onStatusChange?.('closed');
  }

  // Legacy aliases preserved.
  disconnect(): void {
    this.close();
  }

  isOpen(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }
}
