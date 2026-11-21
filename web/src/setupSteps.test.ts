import { describe, expect, it } from 'vitest';
import { freshStatus, nextStep, passwordHint, previousStep, resumeStep, visibleSteps, withStep, type OnboardingStatus } from './setupSteps';

const status = (steps: OnboardingStatus['steps']): OnboardingStatus => ({ completed: false, steps: { account: 'done', ...steps } });

describe('visibleSteps', () => {
  it('shows all four steps to an admin', () => {
    expect(visibleSteps(freshStatus(), true)).toEqual(['sources', 'ai', 'media', 'look']);
  });
  it('hides AI keys from other users, and whatever the server marks not applicable', () => {
    expect(visibleSteps(freshStatus(), false)).toEqual(['sources', 'media', 'look']);
    expect(visibleSteps(status({ sources: 'not_applicable', ai: 'todo', media: 'todo', look: 'todo' }), true)).toEqual(['ai', 'media', 'look']);
  });
});

describe('resumeStep', () => {
  it('starts at the first step', () => {
    expect(resumeStep(freshStatus(), true)).toBe('sources');
  });
  it('resumes at the first step not answered, skipped ones included as answered', () => {
    expect(resumeStep(status({ sources: 'done', ai: 'skipped', media: 'todo', look: 'todo' }), true)).toBe('media');
    expect(resumeStep(status({ sources: 'done', ai: 'todo', media: 'todo', look: 'todo' }), false)).toBe('media');
  });
  it('treats a step the server did not mention as still to do', () => {
    expect(resumeStep(status({ sources: 'done' }), true)).toBe('ai');
  });
  it('opens at the first step when everything was answered', () => {
    expect(resumeStep(status({ sources: 'done', ai: 'done', media: 'skipped', look: 'done' }), true)).toBe('sources');
  });
});

describe('nextStep and previousStep', () => {
  it('walk the visible steps and stop at the ends', () => {
    const s = freshStatus();
    expect(nextStep(s, true, 'sources')).toBe('ai');
    expect(nextStep(s, false, 'sources')).toBe('media');
    expect(nextStep(s, true, 'look')).toBeNull();
    expect(previousStep(s, false, 'media')).toBe('sources');
    expect(previousStep(s, true, 'sources')).toBeNull();
  });
});

describe('withStep', () => {
  it('records an answer without touching the original', () => {
    const before = freshStatus();
    const after = withStep(before, 'ai', 'skipped');
    expect(after.steps.ai).toBe('skipped');
    expect(before.steps.ai).toBe('todo');
  });
});

describe('passwordHint', () => {
  it('counts down to the minimum length', () => {
    expect(passwordHint('abc', 10)).toMatchObject({ ok: false, level: 0 });
    expect(passwordHint('abcdefghi', 10).text).toContain('1 more character ');
  });
  it('rates by length and variety once long enough', () => {
    expect(passwordHint('aaaaaaaaaa', 10).level).toBe(1);
    expect(passwordHint('abcdefgh12', 10).level).toBe(2);
    expect(passwordHint('Abcdefgh12!x', 10).level).toBe(3);
    expect(passwordHint('a long plain phrase', 10).level).toBe(3);
  });
});
