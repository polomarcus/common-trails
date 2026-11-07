import { useEffect, useRef, type RefObject } from 'react';

const FOCUSABLE = [
  'a[href]', 'button:not([disabled])', 'input:not([disabled])',
  'select:not([disabled])', 'textarea:not([disabled])', '[tabindex]:not([tabindex="-1"])',
].join(',');

function focusables(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE))
    .filter((el) => el.getAttribute('aria-hidden') !== 'true');
}

/**
 * Dialog keyboard contract (WAI-ARIA modal):
 *  - on open, focus moves to the first focusable element in `containerRef`;
 *  - Tab / Shift+Tab cycle inside the dialog (focus trap);
 *  - Escape calls `onClose`;
 *  - on close, focus returns to the element focused before opening, or, when
 *    that one is gone (e.g. a menu item unmounted by the click that opened the
 *    dialog), to `returnFocusTo()`.
 */
export function useModalFocus(
  containerRef: RefObject<HTMLElement | null>,
  onClose: () => void,
  returnFocusTo?: () => HTMLElement | null,
): void {
  // Latest callbacks without re-running the mount effect on each render.
  const onCloseRef = useRef(onClose);
  const returnRef = useRef(returnFocusTo);
  onCloseRef.current = onClose;
  returnRef.current = returnFocusTo;

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const root = containerRef.current;
    if (root) (focusables(root)[0] ?? root).focus();

    const onKeyDown = (e: KeyboardEvent) => {
      const container = containerRef.current;
      if (!container) return;
      if (e.key === 'Escape') {
        e.preventDefault();
        onCloseRef.current();
        return;
      }
      if (e.key !== 'Tab') return;
      const items = focusables(container);
      if (items.length === 0) {
        e.preventDefault();
        container.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement as HTMLElement | null;
      const inside = !!active && container.contains(active);
      if (e.shiftKey && (active === first || !inside)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (active === last || !inside)) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown);

    return () => {
      document.removeEventListener('keydown', onKeyDown);
      const target =
        previous && previous.isConnected && previous !== document.body
          ? previous
          : returnRef.current?.() ?? null;
      target?.focus();
    };
  }, [containerRef]);
}
