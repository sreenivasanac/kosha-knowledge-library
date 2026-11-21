/** The setup guide's step logic: which steps a person sees, where they resume, and what comes next. Pure, tested. */

export const STEPS = ['sources', 'ai', 'media', 'look'] as const;
export type Step = (typeof STEPS)[number];
export type StepState = 'done' | 'skipped' | 'todo' | 'not_applicable';
/** `GET /api/onboarding`. `account` is done by the time anyone can ask. */
export interface OnboardingStatus {
  completed: boolean;
  steps: Partial<Record<Step | 'account', StepState>>;
}

export const STEP_TITLES: Record<Step, string> = { sources: 'Connect sources', ai: 'AI keys and budgets', media: 'Media and storage', look: 'Look and keyboard' };

/** What a server without the endpoint is taken to say: nothing done yet. */
export const freshStatus = (): OnboardingStatus => ({ completed: false, steps: { account: 'done', sources: 'todo', ai: 'todo', media: 'todo', look: 'todo' } });

/** The steps shown, in order. The server drops a step with `not_applicable`; AI keys are the admin's alone. */
export function visibleSteps(status: OnboardingStatus, admin: boolean): Step[] {
  return STEPS.filter((s) => status.steps[s] !== 'not_applicable' && (admin || s !== 'ai'));
}

/** Where to resume: the first step still to do, or the first step when all were answered (a later visit from Settings). */
export function resumeStep(status: OnboardingStatus, admin: boolean): Step | null {
  const steps = visibleSteps(status, admin);
  return steps.find((s) => (status.steps[s] ?? 'todo') === 'todo') ?? steps[0] ?? null;
}

/** The step after `current`, or null when `current` is the last one (so the guide finishes). */
export function nextStep(status: OnboardingStatus, admin: boolean, current: Step): Step | null {
  const steps = visibleSteps(status, admin);
  return steps[steps.indexOf(current) + 1] ?? null;
}

export function previousStep(status: OnboardingStatus, admin: boolean, current: Step): Step | null {
  const steps = visibleSteps(status, admin);
  return steps[steps.indexOf(current) - 1] ?? null;
}

/** The status after answering one step, for showing the change before the server confirms it. */
export const withStep = (status: OnboardingStatus, step: Step, state: StepState): OnboardingStatus => ({ ...status, steps: { ...status.steps, [step]: state } });

/** A rough hint, not a gate: length and variety. The server enforces the real rule (the minimum length). */
export function passwordHint(password: string, min: number): { ok: boolean; level: 0 | 1 | 2 | 3; text: string } {
  if (password.length < min) return { ok: false, level: 0, text: `${min - password.length} more character${min - password.length === 1 ? '' : 's'} needed (at least ${min}).` };
  const kinds = [/[a-z]/, /[A-Z]/, /\d/, /[^a-zA-Z\d]/].filter((r) => r.test(password)).length;
  if (password.length >= 16 || (password.length >= 12 && kinds >= 3)) return { ok: true, level: 3, text: 'Strong.' };
  if (kinds >= 2) return { ok: true, level: 2, text: 'Fair. A longer phrase is stronger.' };
  return { ok: true, level: 1, text: 'Weak: add length, or mix letters, digits and symbols.' };
}
