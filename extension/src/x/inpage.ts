// Runs in x.com's own JavaScript world at document_start. Records the bookmark
// responses X requests for itself and hands the raw text to the content script
// (the page's content policy blocks a direct call to the Kosha server).
// It makes no request of its own and changes nothing the page receives.
//
// The XMLHttpRequest hook is ported from twitter-web-exporter (c) prinsss,
// MIT licence: src/core/extensions/manager.ts. The fetch hook is Kosha's.
// See ../../NOTICE.

import { INPAGE_SOURCE, matchOperation } from './match.ts';
import type { InpageMessage } from './match.ts';

function absolute(url: string): string {
  try {
    return new URL(url, location.href).href;
  } catch {
    return url;
  }
}

function forward(url: string, status: number, body: string): void {
  const message: InpageMessage = { source: INPAGE_SOURCE, url, status, body };
  window.postMessage(message, location.origin);
}

const xhrOpen = XMLHttpRequest.prototype.open;
XMLHttpRequest.prototype.open = function (this: XMLHttpRequest, _method: string, rawUrl: string | URL) {
  const url = absolute(String(rawUrl));
  if (matchOperation(url)) {
    this.addEventListener('load', () => {
      try {
        // responseText throws for non-text response types; such a response is not ours.
        forward(url, this.status, this.responseText);
      } catch {
        /* ignore */
      }
    });
  }
  // eslint-disable-next-line prefer-rest-params
  return xhrOpen.apply(this, arguments as any);
} as typeof XMLHttpRequest.prototype.open;

const originalFetch = window.fetch;
window.fetch = async function (this: unknown, ...args: Parameters<typeof fetch>) {
  const response = await originalFetch.apply(this, args);
  try {
    const first = args[0];
    const url = absolute(response.url || (first instanceof Request ? first.url : String(first)));
    if (matchOperation(url)) {
      // Read a copy, so the page still gets its own untouched body.
      response
        .clone()
        .text()
        .then((body) => forward(url, response.status, body))
        .catch(() => {});
    }
  } catch {
    /* never let capture break the page's request */
  }
  return response;
} as typeof fetch;
