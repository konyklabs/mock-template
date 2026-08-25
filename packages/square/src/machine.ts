import { StateMachine, type MachineDef } from '@vendor-unit/core';

/**
 * Order lifecycle.
 *
 * Values from https://developer.squareup.com/reference/square/enums/OrderState:
 *   DRAFT     "Draft orders can be updated, but cannot be paid or fulfilled."
 *   OPEN      "Open orders can be updated."
 *   COMPLETED "Completed orders are fully paid. This is a terminal state."
 *   CANCELED  "Canceled orders are not paid. This is a terminal state."
 *
 * Transitions: CreateOrder defaults to OPEN
 * (https://developer.squareup.com/docs/orders-api/create-orders); DRAFT is
 * moved to OPEN by UpdateOrder (same page); and
 * https://developer.squareup.com/reference/square/orders-api/update-order
 * states "Orders with a COMPLETED or CANCELED state cannot be updated", which
 * is why both terminal states have no outgoing edges here.
 *
 * JUDGMENT: Square publishes no exhaustive transition matrix and no error code
 * for an illegal transition. DRAFT -> CANCELED is allowed here on the reading
 * that an unpaid draft can be abandoned.
 */
export const ORDER_MACHINE: MachineDef = {
  field: 'state',
  initial: 'OPEN',
  states: {
    DRAFT: { summary: 'Not yet payable or fulfillable.', to: ['OPEN', 'CANCELED'] },
    OPEN: { summary: 'Updatable and payable.', to: ['COMPLETED', 'CANCELED'] },
    COMPLETED: { summary: 'Fully paid. Terminal.', terminal: true },
    CANCELED: { summary: 'Not paid. Terminal.', terminal: true },
  },
};

export const orderMachine = new StateMachine(ORDER_MACHINE);
