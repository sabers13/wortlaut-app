/**
 * Typed API Error representation for /vocab endpoints.
 * Conforms to ADR-0002 §4.1 / §5 and ADR-0004 D47 error specifications.
 */

export interface ApiErrorBody {
  detail?: string | Array<{ loc?: unknown[]; msg?: string; type?: string }>;
  picker_token?: string;
  active_token?: string;
  code?: string;
  dictionary_key?: string;
  deck_name?: string;
  [key: string]: unknown;
}

export class ApiError extends Error {
  readonly status: number;
  readonly statusText: string;
  readonly body: unknown;
  readonly detail?: string;
  readonly pickerToken?: string;
  readonly activeToken?: string;
  readonly code?: string;
  readonly dictionaryKey?: string;

  constructor(
    status: number,
    statusText: string,
    body: unknown,
    detail?: string,
    pickerToken?: string,
    activeToken?: string,
    code?: string,
    dictionaryKey?: string,
  ) {
    const message = detail || `API request failed with status ${status} (${statusText})`;
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.statusText = statusText;
    this.body = body;
    this.detail = detail;
    this.pickerToken = pickerToken;
    this.activeToken = activeToken;
    this.code = code;
    this.dictionaryKey = dictionaryKey;

    // Maintain proper prototype chain for instanceof checks
    Object.setPrototypeOf(this, ApiError.prototype);
  }

  get isConflict(): boolean {
    return this.status === 409;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }

  get isUnprocessable(): boolean {
    return this.status === 422;
  }

  get isForbidden(): boolean {
    return this.status === 403;
  }

  get isBadRequest(): boolean {
    return this.status === 400;
  }
}

/**
 * Extracts error details from a non-ok fetch response.
 */
export async function parseApiError(response: Response): Promise<ApiError> {
  const status = response.status;
  const statusText = response.statusText;
  let body: unknown = null;
  let detail: string | undefined;
  let pickerToken: string | undefined;
  let activeToken: string | undefined;
  let code: string | undefined;
  let dictionaryKey: string | undefined;

  try {
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      const json = (await response.json()) as ApiErrorBody;
      body = json;
      if (typeof json.detail === 'string') {
        detail = json.detail;
      } else if (Array.isArray(json.detail) && json.detail.length > 0) {
        detail = json.detail.map((d) => d.msg || JSON.stringify(d)).join('; ');
      }
      if (typeof json.picker_token === 'string') {
        pickerToken = json.picker_token;
      }
      if (typeof json.active_token === 'string') {
        activeToken = json.active_token;
      }
      if (typeof json.code === 'string') {
        code = json.code;
      }
      if (typeof json.dictionary_key === 'string') {
        dictionaryKey = json.dictionary_key;
      }
    } else {
      const text = await response.text();
      body = text;
      detail = text || undefined;
    }
  } catch {
    // If response body cannot be parsed, detail remains undefined
  }

  return new ApiError(status, statusText, body, detail, pickerToken, activeToken, code, dictionaryKey);
}
