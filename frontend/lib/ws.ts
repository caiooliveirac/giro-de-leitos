// Placeholder WebSocket client. Real implementation lands in Phase 7
// (subscription per unit, reconnection with backoff, version reconciliation).

export type UnitWsEvent =
  | { type: 'bed_updated'; payload: unknown }
  | { type: 'counter_updated'; payload: unknown }
  | { type: 'specialist_updated'; payload: unknown }
  | { type: 'exam_updated'; payload: unknown }
  | { type: 'unit_snapshot'; payload: unknown };

export interface UnitWebSocketOptions {
  unitId: string;
  onEvent?: (event: UnitWsEvent) => void;
  onStatusChange?: (status: 'connecting' | 'open' | 'closed') => void;
}

export class UnitWebSocket {
  readonly unitId: string;
  private readonly onEvent?: (event: UnitWsEvent) => void;
  private readonly onStatusChange?: (status: 'connecting' | 'open' | 'closed') => void;

  constructor(options: UnitWebSocketOptions) {
    this.unitId = options.unitId;
    this.onEvent = options.onEvent;
    this.onStatusChange = options.onStatusChange;
  }

  // TODO(phase-7): open ws to /ws/units/:unitId, handle reconnection + heartbeat.
  connect(): void {
    this.onStatusChange?.('connecting');
  }

  // TODO(phase-7): clean close + clear timers.
  disconnect(): void {
    this.onStatusChange?.('closed');
  }
}
