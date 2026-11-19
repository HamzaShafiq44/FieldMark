/** Typed API calls for product category management. */

import { ApiError, apiUrl } from './api'

export interface ProductCategory {
  slug: string
  display_name: string
  emoji: string
  is_active: boolean
  created_at: string
}

interface CategoryPayload {
  name: string
  emoji?: string
}

interface CategoryUpdatePayload {
  name?: string
  emoji?: string
  is_active?: boolean
}

export async function listCategories(): Promise<ProductCategory[]> {
  const response = await fetch(apiUrl('/api/categories'))
  if (!response.ok) throw await responseError(response, 'Categories could not be loaded.')
  const body = (await response.json()) as { items?: unknown }
  if (!Array.isArray(body.items)) {
    throw new Error('The API returned an invalid category response.')
  }
  return body.items as ProductCategory[]
}

export async function createCategory(payload: CategoryPayload): Promise<ProductCategory> {
  const response = await fetch(apiUrl('/api/categories'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) throw await responseError(response, 'Category could not be saved.')
  return (await response.json()) as ProductCategory
}

export async function updateCategory(
  slug: string,
  payload: CategoryUpdatePayload,
): Promise<ProductCategory> {
  const response = await fetch(apiUrl(`/api/categories/${encodeURIComponent(slug)}`), {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) throw await responseError(response, 'Category could not be updated.')
  return (await response.json()) as ProductCategory
}

export async function deleteCategory(slug: string): Promise<void> {
  const response = await fetch(apiUrl(`/api/categories/${encodeURIComponent(slug)}`), {
    method: 'DELETE',
  })
  if (!response.ok) throw await responseError(response, 'Category could not be deleted.')
}

async function responseError(response: Response, fallback: string): Promise<ApiError> {
  const body = (await response.json().catch(() => ({}))) as { detail?: unknown }
  const message = typeof body.detail === 'string' ? body.detail : fallback
  return new ApiError(message, response.status)
}
