import { UnitError } from '../kernel/types.js';

/**
 * Declarative lifecycle. A fork states which values exist and which moves are
 * legal; the core enforces it and produces a core error the vendor shaper turns
 * into that vendor's wording. Adding a lifecycle to a new fork is data, not code.
 */
export interface StateDef {
  summary?: string;
  to?: string[];
  terminal?: boolean;
}

export interface MachineDef {
  /** Entity field holding the state value. */
  field: string;
  initial: string;
  states: Record<string, StateDef>;
}

export class StateMachine {
  constructor(readonly def: MachineDef) {}

  get initial(): string {
    return this.def.initial;
  }

  states(): string[] {
    return Object.keys(this.def.states);
  }

  isTerminal(state: string): boolean {
    return this.def.states[state]?.terminal === true;
  }

  canTransition(from: string, to: string): boolean {
    if (from === to) return true;
    return (this.def.states[from]?.to ?? []).includes(to);
  }

  /** Throws `invalid_transition` / `invalid_value` rather than returning a flag. */
  assertTransition(from: string, to: string, subject: string): void {
    if (!(to in this.def.states)) {
      throw new UnitError('invalid_value', {
        detail: `'${to}' is not a valid ${this.def.field} for ${subject}.`,
        field: this.def.field,
        info: { allowed: this.states() },
      });
    }
    if (from === to) return;
    if (!this.canTransition(from, to)) {
      const terminal = this.isTerminal(from);
      throw new UnitError('invalid_transition', {
        detail: terminal
          ? `${subject} is in the terminal ${this.def.field} ${from} and cannot be updated.`
          : `${subject} cannot move from ${from} to ${to}.`,
        field: this.def.field,
        info: { from, to, terminal, allowed: this.def.states[from]?.to ?? [] },
      });
    }
  }

  /** Any mutation of a terminal entity is refused, not just a state change. */
  assertMutable(from: string, subject: string): void {
    if (this.isTerminal(from)) {
      throw new UnitError('invalid_transition', {
        detail: `${subject} is in the terminal ${this.def.field} ${from} and cannot be updated.`,
        field: this.def.field,
        info: { from, terminal: true },
      });
    }
  }
}
