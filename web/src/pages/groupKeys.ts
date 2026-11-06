import type { Decision } from "../types";

export interface KeyMember {
  file_id: string;
  decision: Decision;
}

export interface KeyState {
  members: KeyMember[];
  selected: number;
  anchor: number | null;
}

export type KeyAction = { action: "confirm" | "skip" | "unrelated" };

function withDecision(state: KeyState, decision: Decision): KeyState {
  return {
    ...state,
    members: state.members.map((m, i) => (i === state.selected ? { ...m, decision } : m)),
  };
}

export function applyKey(state: KeyState, key: string): KeyState | KeyAction {
  if (/^[1-9]$/.test(key)) {
    const index = Number(key) - 1;
    return index < state.members.length ? { ...state, selected: index } : state;
  }
  switch (key) {
    case "ArrowDown":
      return { ...state, selected: Math.min(state.selected + 1, state.members.length - 1) };
    case "ArrowUp":
      return { ...state, selected: Math.max(state.selected - 1, 0) };
    case "k":
      return withDecision(state, "keep");
    case "d":
      return withDecision(state, "delete");
    case "a":
      return { ...state, anchor: state.selected };
    case "u":
      return state.anchor !== null && state.anchor !== state.selected ? { action: "unrelated" } : state;
    case "Enter":
      return state.members.every((m) => m.decision !== "undecided") ? { action: "confirm" } : state;
    case "Escape":
      return { action: "skip" };
    default:
      return state;
  }
}
