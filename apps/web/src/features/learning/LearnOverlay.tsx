import type { CourseDTO } from '@ci/contracts';
import { useCallback, useEffect, useState } from 'react';
import { api } from '../../lib/api';
import { keys, useAction, useLearning } from '../../lib/queries';
import { Button, cx, Dot, ErrorState, Modal, Skeleton } from '../../ui';
import s from './Learning.module.css';

type Mode = 'cards' | 'quiz' | 'done';

/** A course deck: cards → quiz → score, then the completion is recorded against the person's file. */
export function LearnOverlay({ courseId, onClose }: { courseId: string | null; onClose: () => void }) {
  const learning = useLearning();
  const course = courseId ? learning.data?.courses.find((c) => c.id === courseId) : undefined;
  const open = courseId !== null;

  return (
    <Modal
      open={open}
      onClose={onClose}
      width={560}
      title={
        <span className={s.headTitle}>
          <Dot color="var(--accent)" pulse />
          <span>{course?.title ?? 'Learning'}</span>
          {course && <span className={s.headTime}>· {course.minutes} min</span>}
        </span>
      }
    >
      {course ? (
        <Deck key={course.id} course={course} onClose={onClose} />
      ) : learning.error ? (
        <ErrorState error={learning.error} onRetry={() => void learning.refetch()} />
      ) : learning.isLoading ? (
        <Skeleton h={220} />
      ) : (
        <p className={s.meta}>This course is no longer available.</p>
      )}
    </Modal>
  );
}

function Deck({ course, onClose }: { course: CourseDTO; onClose: () => void }) {
  const [mode, setMode] = useState<Mode>(course.cards.length ? 'cards' : 'quiz');
  const [idx, setIdx] = useState(0);
  const [qi, setQi] = useState(0);
  const [picks, setPicks] = useState<number[]>([]);

  const lastCard = idx >= course.cards.length - 1;
  const next = useCallback(() => {
    if (!lastCard) setIdx((i) => i + 1);
    else if (course.quiz.length) setMode('quiz');
    else setMode('done');
  }, [lastCard, course.quiz.length]);
  const prev = useCallback(() => setIdx((i) => Math.max(0, i - 1)), []);

  useEffect(() => {
    if (mode !== 'cards') return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT')) return;
      if (e.key === 'ArrowRight') {
        e.preventDefault();
        next();
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault();
        prev();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [mode, next, prev]);

  const complete = useAction(
    (answers: number[]) =>
      api.post<{ score: number; total: number }>(`/v1/courses/${course.id}/complete`, { answers }),
    {
      invalidate: [keys.learning, keys.me],
      success: `Marked complete — your record on ${course.title} is updated.`,
    },
  );

  if (mode === 'cards') {
    const card = course.cards[idx]!;
    return (
      <div>
        <div key={idx} className={s.card}>
          <div className={s.step}>
            Card {idx + 1} of {course.cards.length}
          </div>
          <h3 className={s.cardTitle}>{card.title}</h3>
          <p className={s.cardBody}>{card.body}</p>
        </div>
        <div className={s.nav}>
          {idx > 0 ? <Button onClick={prev}>← Back</Button> : <span style={{ width: 72 }} />}
          <div className={s.dots} aria-hidden>
            {course.cards.map((_, i) => (
              <span
                key={i}
                className={s.navDot}
                style={{ background: i === idx ? 'var(--accent)' : i < idx ? 'var(--ok)' : 'var(--line)' }}
              />
            ))}
          </div>
          <Button variant="primary" onClick={next} data-autofocus>
            {!lastCard ? 'Next →' : course.quiz.length ? 'Take the quiz →' : 'Finish →'}
          </Button>
        </div>
        <div className={s.keys}>Use ← and → to move between cards.</div>
      </div>
    );
  }

  if (mode === 'quiz') {
    const q = course.quiz[qi]!;
    const picked = picks[qi];
    const answered = picked !== undefined;
    const right = answered && picked === q.correct;
    const lastQ = qi >= course.quiz.length - 1;
    return (
      <div>
        <div className={cx(s.step, s.stepQuiz)}>
          Question {qi + 1} of {course.quiz.length}
        </div>
        <h3 className={s.question} id={`q-${qi}`}>
          {q.q}
        </h3>
        <div className={s.opts} role="group" aria-labelledby={`q-${qi}`}>
          {q.options.map((o, i) => {
            const isRight = answered && i === q.correct;
            const isWrong = answered && i === picked && i !== q.correct;
            return (
              <button
                key={i}
                type="button"
                className={cx(s.opt, isRight && s.optRight, isWrong && s.optWrong)}
                disabled={answered}
                aria-pressed={picked === i}
                onClick={() =>
                  setPicks((p) => {
                    const n = p.slice();
                    n[qi] = i;
                    return n;
                  })
                }
              >
                <span className={s.optLabel}>{o}</span>
                {isRight && (
                  <span className={s.mark} style={{ color: 'var(--ok)' }}>
                    ✓<span className="sr-only"> correct answer</span>
                  </span>
                )}
                {isWrong && (
                  <span className={s.mark} style={{ color: 'var(--bad)' }}>
                    ×<span className="sr-only"> your answer, incorrect</span>
                  </span>
                )}
              </button>
            );
          })}
        </div>
        {answered && (
          <div className={s.feedback} role="status">
            <span className={s.feedbackText} style={{ color: right ? 'var(--ok)' : 'var(--bad-text)' }}>
              {right
                ? `Right. ${q.options[q.correct]}`
                : `Not quite — the answer is: ${q.options[q.correct]}`}
            </span>
            <Button variant="dark" onClick={() => (lastQ ? setMode('done') : setQi(qi + 1))} autoFocus>
              {lastQ ? 'Finish' : 'Next question →'}
            </Button>
          </div>
        )}
      </div>
    );
  }

  const score = course.quiz.filter((q, i) => picks[i] === q.correct).length;
  return (
    <div className={s.done}>
      <div className={s.doneIcon} aria-hidden>
        <svg width="20" height="20" viewBox="0 0 16 16" fill="none">
          <path
            d="M3.5 8.4l3 3 6-6.4"
            stroke="var(--ok-dot)"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeDasharray="26"
            style={{ animation: 'drawCheck .45s ease .05s both' }}
          />
        </svg>
      </div>
      <div className={s.doneScore}>
        {score} of {course.quiz.length} correct
      </div>
      <div className={s.doneText}>Mark it complete to record it against your training file.</div>
      <div className={s.doneActions}>
        <Button
          onClick={() => {
            setMode(course.cards.length ? 'cards' : 'quiz');
            setIdx(0);
            setQi(0);
            setPicks([]);
          }}
        >
          Start again
        </Button>
        <Button
          variant="dark"
          loading={complete.isPending}
          onClick={() =>
            complete.mutate(
              course.quiz.map((_, i) => picks[i] ?? 0),
              { onSuccess: onClose },
            )
          }
        >
          Mark complete
        </Button>
      </div>
    </div>
  );
}
