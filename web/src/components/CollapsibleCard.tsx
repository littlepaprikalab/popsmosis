'use client';

import { useState, useRef, ReactNode } from 'react';

/** Chevron on the card header: points down when open, right when collapsed. */
function CollapseChevron({ collapsed }: { collapsed: boolean }) {
  return (
    <svg
      width="16" height="16" viewBox="0 0 16 16" fill="none"
      className="transition-transform duration-200 shrink-0"
      style={{ color: 'var(--text-secondary)', transform: collapsed ? 'rotate(-90deg)' : 'rotate(0deg)' }}
      aria-hidden="true"
    >
      <path d="M4 6L8 10L12 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** Small inline chevron for the foot of a card body, so a long card can be
 *  closed without scrolling back up to its header. */
export function CollapseButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={(e) => { e.stopPropagation(); onClick(); }}
      className="p-1 -m-1 hover-text transition-colors"
      style={{ color: 'var(--text-muted)' }}
      aria-label="Collapse"
    >
      <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <path d="M4 10L8 6L12 10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </button>
  );
}

const FOLD_MS = 300;

/**
 * Style for a folding region. Grid rows animate to the content's own height
 * without measuring it; `visibility` waits out the fold, then takes the folded
 * content out of the accessibility tree and the tab order — `overflow: hidden`
 * alone only stops it being painted.
 */
function fold(folded: boolean): React.CSSProperties {
  return {
    gridTemplateRows: folded ? '0fr' : '1fr',
    opacity: folded ? 0 : 1,
    visibility: folded ? 'hidden' : 'visible',
    transition: `grid-template-rows ${FOLD_MS}ms ease-in-out, opacity ${FOLD_MS}ms ease-in-out, visibility 0s linear ${folded ? FOLD_MS : 0}ms`,
  };
}

interface Props {
  /** Always-visible header. Receives `collapsed` so it can clamp its own content. */
  header: (collapsed: boolean) => ReactNode;
  /** Optional stand-in shown under the header while collapsed (e.g. an answer snippet). */
  preview?: ReactNode;
  /** Card body. Receives a collapse callback so the trigger can sit inline. */
  children: ReactNode | ((collapse: () => void) => ReactNode);
  className?: string;
  style?: React.CSSProperties;
  /** Header padding. Defaults to 'p-4'. */
  headerClassName?: string;
  /** Body padding. Defaults to 'px-4 pb-4'. */
  bodyClassName?: string;
  defaultCollapsed?: boolean;
  /** Set to hold the folded state outside, e.g. so a link elsewhere on the page can open the card. */
  collapsed?: boolean;
  onCollapsedChange?: (collapsed: boolean) => void;
  id?: string;
  /** The id of the card's heading: the card is then a section, a landmark named by it. */
  labelledBy?: string;
  testId?: string;
}

/**
 * A card whose body folds away when its header is clicked. Both the body and
 * the collapsed preview animate through grid rows, which transitions to the
 * content's own height without measuring it. Shape follows sparks'
 * CollapsibleCard.
 */
export function CollapsibleCard({
  header,
  preview,
  children,
  className,
  style,
  headerClassName,
  bodyClassName,
  defaultCollapsed = false,
  collapsed: held,
  onCollapsedChange,
  id,
  labelledBy,
  testId,
}: Props) {
  const [own, setOwn] = useState(defaultCollapsed);
  const collapsed = held ?? own;
  const setCollapsed = (next: boolean) => {
    if (held === undefined) setOwn(next);
    onCollapsedChange?.(next);
  };
  const cardRef = useRef<HTMLElement>(null);
  const headerRef = useRef<HTMLDivElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const toggle = () => setCollapsed(!collapsed);

  // Folding from inside the body, the chevron at its foot: by then a long card's header can be far
  // above the screen, and what is below would slide up in its place, losing the reader. So bring the
  // header back near the top of the screen first, only if it is off-screen, and hand focus to its
  // chevron: the button just pressed is about to be hidden.
  const collapse = () => {
    const card = cardRef.current;
    if (card) {
      const gap = parseFloat(getComputedStyle(card).scrollMarginTop) || 0;
      if (card.getBoundingClientRect().top < gap) card.scrollIntoView({ block: 'start' });
      if (card.contains(document.activeElement)) toggleRef.current?.focus({ preventScroll: true });
    }
    setCollapsed(true);
  };

  // A click that ends a selection in the header is the reader copying the text,
  // not asking to fold the card. Scoped to this header: a leftover selection
  // somewhere else on the page must not swallow the click.
  const headerClick = () => {
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed && headerRef.current?.contains(sel.anchorNode)) return;
    toggle();
  };

  const Root = labelledBy ? 'section' : 'div';
  return (
    // scroll-mt-20: room above the card, clear of the nav, when it is scrolled back to (as #comments has).
    <Root ref={cardRef as React.RefObject<HTMLDivElement>} id={id} aria-labelledby={labelledBy}
      className={`rounded-lg overflow-hidden scroll-mt-20 ${className ?? ''}`} style={style} data-testid={testId}>
      {/* Header — always visible. Anywhere in it toggles the body, and the
          chevron is the real button, so the header text is still selectable
          and screen readers read it as text rather than as a control's name. */}
      <div
        ref={headerRef}
        onClick={headerClick}
        className={`flex items-start justify-between gap-2 cursor-pointer ${headerClassName ?? 'p-4'}`}
      >
        <div className="min-w-0 flex-1">{header(collapsed)}</div>
        <button
          ref={toggleRef}
          type="button"
          onClick={(e) => { e.stopPropagation(); toggle(); }}
          aria-expanded={!collapsed}
          aria-label={collapsed ? 'Expand card' : 'Collapse card'}
          className="p-1 -m-1 shrink-0"
        >
          <CollapseChevron collapsed={collapsed} />
        </button>
      </div>

      {/* Collapsed preview — the inverse of the body */}
      {preview && (
        <div className="grid" style={fold(!collapsed)}>
          <div className="overflow-hidden">
            <div className="px-4 pb-4">{preview}</div>
          </div>
        </div>
      )}

      {/* Body */}
      <div className="grid" style={fold(collapsed)}>
        <div className="overflow-hidden">
          <div className={bodyClassName ?? 'px-4 pb-4'}>
            {typeof children === 'function' ? children(collapse) : children}
          </div>
        </div>
      </div>
    </Root>
  );
}
