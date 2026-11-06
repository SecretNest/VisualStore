import type { Decision } from "../types";

export type CompareMode = "side" | "overlay" | "blink";

export interface KeyMember {
  file_id: string;
  decision: Decision;
}

export interface KeyState {
  members: KeyMember[];
  selected: number;
  anchor: number | null;
  mode: CompareMode;
  target: number | null; // member compared against the selected one; null = nearest neighbour
}

export type KeyAction = { action: "confirm" | "skip" | "unrelated" };

function withDecision(state: KeyState, decision: Decision): KeyState {
  return {
    ...state,
    members: state.members.map((m, i) => (i === state.selected ? { ...m, decision } : m)),
  };
}

export function selectMember(state: KeyState, index: number): KeyState {
  return index >= 0 && index < state.members.length ? { ...state, selected: index, target: null } : state;
}

export function compareTarget(state: KeyState, nearest: number | null): number | null {
  return state.target ?? nearest;
}

function cycleTarget(state: KeyState, nearest: number | null, step: 1 | -1): KeyState {
  const n = state.members.length;
  if (n < 2) return state;
  const start = compareTarget(state, nearest) ?? state.selected;
  let index = start;
  for (let i = 0; i < n; i++) {
    index = (index + step + n) % n;
    if (index !== state.selected) return { ...state, target: index };
  }
  return state;
}

export function applyKey(state: KeyState, key: string, nearest: number | null = null): KeyState | KeyAction {
  if (/^[1-9]$/.test(key)) {
    return selectMember(state, Number(key) - 1);
  }
  switch (key) {
    case "ArrowDown":
      return { ...state, selected: Math.min(state.selected + 1, state.members.length - 1), target: null };
    case "ArrowUp":
      return { ...state, selected: Math.max(state.selected - 1, 0), target: null };
    case "k":
      return withDecision(state, "keep");
    case "d":
      return withDecision(state, "delete");
    case "a":
      return { ...state, anchor: state.selected };
    case "u":
      return state.anchor !== null && state.anchor !== state.selected ? { action: "unrelated" } : state;
    case "s":
      return { ...state, mode: "side" };
    case "o":
      return { ...state, mode: "overlay" };
    case "b":
      return { ...state, mode: "blink" };
    case "n":
      return cycleTarget(state, nearest, 1);
    case "p":
      return cycleTarget(state, nearest, -1);
    case " ":
      return state.target === null ? state : { ...state, selected: state.target, target: state.selected };
    case "Enter":
      return state.members.every((m) => m.decision !== "undecided") ? { action: "confirm" } : state;
    case "Escape":
      return { action: "skip" };
    default:
      return state;
  }
}
