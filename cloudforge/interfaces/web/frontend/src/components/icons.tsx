/**
 * CloudForge icon system.
 *
 * Hand-drawn SVG set on a 24-unit grid, 1.6 stroke, round joins, currentColor
 * so every icon inherits the semantic colour of its row. No emoji, no icon
 * font dependency.
 */
import React from 'react';

interface IconProps {
  size?: number;
  className?: string;
  strokeWidth?: number;
}

const base = (size: number): React.SVGProps<SVGSVGElement> => ({
  width: size,
  height: size,
  viewBox: '0 0 24 24',
  fill: 'none',
  xmlns: 'http://www.w3.org/2000/svg',
});

const stroke = (w: number) => ({
  stroke: 'currentColor',
  strokeWidth: w,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
});

/* ── Brand mark ──────────────────────────────────────────────────────
   A forge anvil seated inside a hex module outline, struck by a spark:
   infrastructure (hex node) + forging (anvil) + the agent's edit (spark).
   ------------------------------------------------------------------ */
export function CloudForgeMark({ size = 24, className }: IconProps) {
  const id = React.useId();
  return (
    <svg {...base(size)} className={className} aria-hidden="true">
      <defs>
        <linearGradient id={`${id}-g`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#f7a429" />
          <stop offset="55%" stopColor="#ff6b6b" />
          <stop offset="100%" stopColor="#a78bfa" />
        </linearGradient>
      </defs>
      {/* hex module shell */}
      <path
        d="M12 2.2 20.3 6.9 V17.1 L12 21.8 3.7 17.1 V6.9 Z"
        stroke={`url(#${id}-g)`}
        strokeWidth={1.5}
        strokeLinejoin="round"
        fill="none"
      />
      {/* anvil: horned top plate, waist, splayed base */}
      <path
        d="M5.2 10.8 7.5 9.5 H17.6 L16.1 11.9 H8.4 Z
           M10.7 11.9 H13.3 L12.8 14.4 H11.2 Z
           M8.7 14.4 H15.3 L16.5 16.9 H7.5 Z"
        fill={`url(#${id}-g)`}
      />
      {/* forge spark off the hammer strike */}
      <path d="M16.9 4.7 17.5 6.2 19 6.8 17.5 7.4 16.9 8.9 16.3 7.4 14.8 6.8 16.3 6.2 Z"
            fill={`url(#${id}-g)`} opacity={0.9} />
    </svg>
  );
}

/* ── Run lifecycle ─────────────────────────────────────────────────── */

// context_loaded — stacked infrastructure layers
export function IconLayers({ size = 16, className, strokeWidth = 1.6 }: IconProps) {
  return (
    <svg {...base(size)} className={className} aria-hidden="true">
      <path d="M12 3 20.5 7.5 12 12 3.5 7.5 Z" {...stroke(strokeWidth)} />
      <path d="M3.5 12 12 16.5 20.5 12" {...stroke(strokeWidth)} />
      <path d="M3.5 16.5 12 21 20.5 16.5" {...stroke(strokeWidth)} opacity={0.55} />
    </svg>
  );
}

// classified — routing target
export function IconTarget({ size = 16, className, strokeWidth = 1.6 }: IconProps) {
  return (
    <svg {...base(size)} className={className} aria-hidden="true">
      <circle cx="12" cy="12" r="8.2" {...stroke(strokeWidth)} />
      <circle cx="12" cy="12" r="3.6" {...stroke(strokeWidth)} />
      <path d="M12 1.6 V4.6 M12 19.4 V22.4 M1.6 12 H4.6 M19.4 12 H22.4" {...stroke(strokeWidth)} />
    </svg>
  );
}

// agent_start — agent compute unit
export function IconAgent({ size = 16, className, strokeWidth = 1.6 }: IconProps) {
  return (
    <svg {...base(size)} className={className} aria-hidden="true">
      <rect x="4.5" y="4.5" width="15" height="15" rx="3.5" {...stroke(strokeWidth)} />
      <rect x="9" y="9" width="6" height="6" rx="1.4" {...stroke(strokeWidth)} />
      <path d="M9.5 1.9 V4.5 M14.5 1.9 V4.5 M9.5 19.5 V22.1 M14.5 19.5 V22.1
               M1.9 9.5 H4.5 M1.9 14.5 H4.5 M19.5 9.5 H22.1 M19.5 14.5 H22.1"
            {...stroke(strokeWidth)} opacity={0.75} />
    </svg>
  );
}

// success — check in a ring
export function IconCheck({ size = 16, className, strokeWidth = 1.6 }: IconProps) {
  return (
    <svg {...base(size)} className={className} aria-hidden="true">
      <circle cx="12" cy="12" r="8.6" {...stroke(strokeWidth)} />
      <path d="M8.2 12.3 10.9 15 15.9 9.4" {...stroke(strokeWidth)} />
    </svg>
  );
}

// emulator_start — test flask
export function IconEmulator({ size = 16, className, strokeWidth = 1.6 }: IconProps) {
  return (
    <svg {...base(size)} className={className} aria-hidden="true">
      <path d="M9.6 2.8 V9.1 L4.6 17.4 A2 2 0 0 0 6.3 20.5 H17.7 A2 2 0 0 0 19.4 17.4 L14.4 9.1 V2.8"
            {...stroke(strokeWidth)} />
      <path d="M8.4 2.8 H15.6" {...stroke(strokeWidth)} />
      <path d="M7 15.4 H17" {...stroke(strokeWidth)} opacity={0.6} />
    </svg>
  );
}

// failure — cross in a ring
export function IconFail({ size = 16, className, strokeWidth = 1.6 }: IconProps) {
  return (
    <svg {...base(size)} className={className} aria-hidden="true">
      <circle cx="12" cy="12" r="8.6" {...stroke(strokeWidth)} />
      <path d="M9.2 9.2 14.8 14.8 M14.8 9.2 9.2 14.8" {...stroke(strokeWidth)} />
    </svg>
  );
}

// fix_start — wrench
export function IconFix({ size = 16, className, strokeWidth = 1.6 }: IconProps) {
  return (
    <svg {...base(size)} className={className} aria-hidden="true">
      <path d="M15.3 3.4 A5.4 5.4 0 0 0 9.1 11.3 L3.8 16.6 A2.1 2.1 0 0 0 6.8 19.6 L12.1 14.3
               A5.4 5.4 0 0 0 20 8.1 L16.7 11.4 13.4 10.5 12.5 7.2 Z"
            {...stroke(strokeWidth)} />
    </svg>
  );
}

// diff_ready — document with +/- gutter
export function IconDiff({ size = 16, className, strokeWidth = 1.6 }: IconProps) {
  return (
    <svg {...base(size)} className={className} aria-hidden="true">
      <path d="M6 2.8 H14 L18.6 7.4 V19.1 A2.1 2.1 0 0 1 16.5 21.2 H6 A2.1 2.1 0 0 1 3.9 19.1
               V4.9 A2.1 2.1 0 0 1 6 2.8 Z" {...stroke(strokeWidth)} />
      <path d="M13.8 2.9 V7.6 H18.5" {...stroke(strokeWidth)} />
      <path d="M7.4 12.4 H11.4 M9.4 10.4 V14.4 M7.4 17.4 H11.4" {...stroke(strokeWidth)} />
    </svg>
  );
}

// escalate — alert triangle
export function IconEscalate({ size = 16, className, strokeWidth = 1.6 }: IconProps) {
  return (
    <svg {...base(size)} className={className} aria-hidden="true">
      <path d="M12 3.6 21.3 19.4 A1.5 1.5 0 0 1 20 21.6 H4 A1.5 1.5 0 0 1 2.7 19.4 Z"
            {...stroke(strokeWidth)} />
      <path d="M12 9.6 V14" {...stroke(strokeWidth)} />
      <circle cx="12" cy="17.4" r="0.95" fill="currentColor" />
    </svg>
  );
}

// error — octagon
export function IconError({ size = 16, className, strokeWidth = 1.6 }: IconProps) {
  return (
    <svg {...base(size)} className={className} aria-hidden="true">
      <path d="M8.3 2.9 H15.7 L21.1 8.3 V15.7 L15.7 21.1 H8.3 L2.9 15.7 V8.3 Z"
            {...stroke(strokeWidth)} />
      <path d="M12 7.8 V13" {...stroke(strokeWidth)} />
      <circle cx="12" cy="16.3" r="0.95" fill="currentColor" />
    </svg>
  );
}

/* ── Agent domains ─────────────────────────────────────────────────── */

// pipeline — CI graph
export function IconPipeline({ size = 16, className, strokeWidth = 1.6 }: IconProps) {
  return (
    <svg {...base(size)} className={className} aria-hidden="true">
      <circle cx="5.4" cy="12" r="2.6" {...stroke(strokeWidth)} />
      <circle cx="18.6" cy="6.2" r="2.6" {...stroke(strokeWidth)} />
      <circle cx="18.6" cy="17.8" r="2.6" {...stroke(strokeWidth)} />
      <path d="M7.9 11 C11.6 9.6 13.2 7.6 16 6.5 M7.9 13 C11.6 14.4 13.2 16.4 16 17.5"
            {...stroke(strokeWidth)} />
    </svg>
  );
}

// iac — module cube
export function IconCube({ size = 16, className, strokeWidth = 1.6 }: IconProps) {
  return (
    <svg {...base(size)} className={className} aria-hidden="true">
      <path d="M12 2.6 20.4 7.2 V16.8 L12 21.4 3.6 16.8 V7.2 Z" {...stroke(strokeWidth)} />
      <path d="M3.6 7.2 12 11.9 20.4 7.2 M12 11.9 V21.4" {...stroke(strokeWidth)} />
    </svg>
  );
}

// security — shield
export function IconShield({ size = 16, className, strokeWidth = 1.6 }: IconProps) {
  return (
    <svg {...base(size)} className={className} aria-hidden="true">
      <path d="M12 2.6 19.6 5.6 V11.4 C19.6 16.3 16.4 19.9 12 21.4 7.6 19.9 4.4 16.3 4.4 11.4
               V5.6 Z" {...stroke(strokeWidth)} />
      <path d="M8.9 11.9 11.3 14.3 15.3 9.9" {...stroke(strokeWidth)} />
    </svg>
  );
}

// observability — signal pulse
export function IconPulse({ size = 16, className, strokeWidth = 1.6 }: IconProps) {
  return (
    <svg {...base(size)} className={className} aria-hidden="true">
      <path d="M2.6 12.6 H7 L9.4 6.4 12.6 17.6 15.2 12.6 H21.4" {...stroke(strokeWidth)} />
    </svg>
  );
}

/* ── UI chrome ─────────────────────────────────────────────────────── */

export function IconBolt({ size = 16, className, strokeWidth = 1.6 }: IconProps) {
  return (
    <svg {...base(size)} className={className} aria-hidden="true">
      <path d="M13.4 2.4 4.9 13.3 H11 L10.6 21.6 19.1 10.7 H13 Z" {...stroke(strokeWidth)} />
    </svg>
  );
}

export function IconSettings({ size = 16, className, strokeWidth = 1.6 }: IconProps) {
  return (
    <svg {...base(size)} className={className} aria-hidden="true">
      <path d="M4 7.4 H13 M17.4 7.4 H20 M4 16.6 H8.6 M13 16.6 H20" {...stroke(strokeWidth)} />
      <circle cx="15.2" cy="7.4" r="2.4" {...stroke(strokeWidth)} />
      <circle cx="10.8" cy="16.6" r="2.4" {...stroke(strokeWidth)} />
    </svg>
  );
}

export function IconBranch({ size = 16, className, strokeWidth = 1.6 }: IconProps) {
  return (
    <svg {...base(size)} className={className} aria-hidden="true">
      <circle cx="6.8" cy="5.4" r="2.4" {...stroke(strokeWidth)} />
      <circle cx="6.8" cy="18.6" r="2.4" {...stroke(strokeWidth)} />
      <circle cx="17.2" cy="9.2" r="2.4" {...stroke(strokeWidth)} />
      <path d="M6.8 7.8 V16.2 M17.2 11.6 C17.2 15 14 15.2 11.4 15.8 9.6 16.2 8.6 17 8.2 17.8"
            {...stroke(strokeWidth)} />
    </svg>
  );
}

export function IconDoc({ size = 16, className, strokeWidth = 1.6 }: IconProps) {
  return (
    <svg {...base(size)} className={className} aria-hidden="true">
      <path d="M6 2.8 H14 L18.6 7.4 V19.1 A2.1 2.1 0 0 1 16.5 21.2 H6 A2.1 2.1 0 0 1 3.9 19.1
               V4.9 A2.1 2.1 0 0 1 6 2.8 Z" {...stroke(strokeWidth)} />
      <path d="M13.8 2.9 V7.6 H18.5 M7.6 12.4 H14.4 M7.6 16.4 H12.4" {...stroke(strokeWidth)} />
    </svg>
  );
}

export function IconArrowUpRight({ size = 14, className, strokeWidth = 1.8 }: IconProps) {
  return (
    <svg {...base(size)} className={className} aria-hidden="true">
      <path d="M7.4 16.6 16.6 7.4 M8.8 7.4 H16.6 V15.2" {...stroke(strokeWidth)} />
    </svg>
  );
}

export function IconCheckMark({ size = 14, className, strokeWidth = 2 }: IconProps) {
  return (
    <svg {...base(size)} className={className} aria-hidden="true">
      <path d="M5 12.6 9.6 17.2 19 6.8" {...stroke(strokeWidth)} />
    </svg>
  );
}

export function IconXMark({ size = 14, className, strokeWidth = 2 }: IconProps) {
  return (
    <svg {...base(size)} className={className} aria-hidden="true">
      <path d="M6.4 6.4 17.6 17.6 M17.6 6.4 6.4 17.6" {...stroke(strokeWidth)} />
    </svg>
  );
}

/** Run status indicator — a ring that only fills once the run settles. */
export function IconStatusDot({ status, size = 10 }: { status: string; size?: number }) {
  const tone: Record<string, string> = {
    running: 'var(--amber)',
    pending: 'var(--text-3)',
    complete: 'var(--green)',
    pr_opened: 'var(--green)',
    error: 'var(--red)',
    escalated: 'var(--red)',
  };
  const color = tone[status] ?? 'var(--text-3)';
  const hollow = status === 'pending' || status === 'running';
  return (
    <svg width={size} height={size} viewBox="0 0 12 12" fill="none" aria-hidden="true"
         className={status === 'running' ? 'cf-dot-pulse' : undefined}>
      <circle cx="6" cy="6" r="4.2" stroke={color} strokeWidth={1.8}
              fill={hollow ? 'none' : color} />
    </svg>
  );
}

/** Maps an agent domain to its icon. */
export function AgentIcon({ agent, size = 13, className }: { agent: string } & IconProps) {
  switch (agent) {
    case 'pipeline':      return <IconPipeline size={size} className={className} />;
    case 'iac':           return <IconCube size={size} className={className} />;
    case 'security':      return <IconShield size={size} className={className} />;
    case 'observability': return <IconPulse size={size} className={className} />;
    default:              return <IconDoc size={size} className={className} />;
  }
}
