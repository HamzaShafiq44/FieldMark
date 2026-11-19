import {
  CircleAlert,
  Check,
  LoaderCircle,
  Pencil,
  Plus,
  Power,
  RefreshCw,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  createCategory,
  deleteCategory,
  listCategories,
  updateCategory,
  type ProductCategory,
} from '../services/categories'

const MAX_EMOJI_LENGTH = 8
const SUGGESTED_FRUITS = [
  'Apple',
  'Apricot',
  'Avocado',
  'Banana',
  'Blackberry',
  'Blueberry',
  'Cantaloupe',
  'Cherry',
  'Clementine',
  'Coconut',
  'Cranberry',
  'Date',
  'Dragonfruit',
  'Fig',
  'Gooseberry',
  'Grape',
  'Grapefruit',
  'Guava',
  'Honeydew',
  'Kiwi',
  'Kumquat',
  'Lemon',
  'Lime',
  'Lychee',
  'Mango',
  'Melon',
  'Nectarine',
  'Orange',
  'Papaya',
  'Passionfruit',
  'Peach',
  'Pear',
  'Persimmon',
  'Pineapple',
  'Plum',
  'Pomegranate',
  'Raspberry',
  'Strawberry',
  'Tangerine',
  'Tomato',
  'Watermelon',
]

function formatCreatedAt(value: string): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date)
}

function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof Error ? caught.message : fallback
}

function CategoriesPage() {
  const [categories, setCategories] = useState<ProductCategory[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [emoji, setEmoji] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [editingSlug, setEditingSlug] = useState<string | null>(null)
  const [editName, setEditName] = useState('')
  const [editEmoji, setEditEmoji] = useState('')
  const [isSavingEdit, setIsSavingEdit] = useState(false)
  const [actionSlug, setActionSlug] = useState<string | null>(null)
  const [cardError, setCardError] = useState<string | null>(null)
  const activeCount = categories.filter((category) => category.is_active).length

  const loadCategories = useCallback(async (showLoading = false) => {
    if (showLoading) setIsLoading(true)
    try {
      const items = await listCategories()
      setCategories(items)
      setLoadError(null)
    } catch (caught: unknown) {
      setLoadError(errorMessage(caught, 'Categories could not be loaded.'))
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    let active = true
    listCategories()
      .then((items) => {
        if (active) setCategories(items)
      })
      .catch((caught: unknown) => {
        if (active) setLoadError(errorMessage(caught, 'Categories could not be loaded.'))
      })
      .finally(() => {
        if (active) setIsLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  const handleAdd = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (isSubmitting) return
    const trimmedName = name.trim()
    if (trimmedName.length < 2) {
      setFormError('Enter a category name with at least 2 characters.')
      return
    }
    setIsSubmitting(true)
    setFormError(null)
    setSuccessMessage(null)
    try {
      const created = await createCategory({ name: trimmedName, emoji: emoji.trim() })
      setCategories((current) => [...current, created])
      setName('')
      setEmoji('')
      setSuccessMessage(`${created.display_name} was added and is now used for inspections.`)
    } catch (caught: unknown) {
      setFormError(errorMessage(caught, 'Category could not be saved.'))
    } finally {
      setIsSubmitting(false)
    }
  }

  const startEditing = (category: ProductCategory) => {
    setEditingSlug(category.slug)
    setEditName(category.display_name)
    setEditEmoji(category.emoji)
    setCardError(null)
    setSuccessMessage(null)
  }

  const cancelEditing = () => {
    setEditingSlug(null)
    setEditName('')
    setEditEmoji('')
  }

  const handleSaveEdit = async (category: ProductCategory) => {
    if (isSavingEdit) return
    const trimmedName = editName.trim()
    if (trimmedName.length < 2) {
      setCardError('Enter a category name with at least 2 characters.')
      return
    }
    setIsSavingEdit(true)
    setCardError(null)
    try {
      const updated = await updateCategory(category.slug, {
        name: trimmedName,
        emoji: editEmoji.trim(),
      })
      setCategories((current) =>
        current.map((item) => (item.slug === updated.slug ? updated : item)),
      )
      cancelEditing()
      setSuccessMessage(`${updated.display_name} was updated.`)
    } catch (caught: unknown) {
      setCardError(errorMessage(caught, 'Category could not be updated.'))
    } finally {
      setIsSavingEdit(false)
    }
  }

  const handleToggleActive = async (category: ProductCategory) => {
    if (actionSlug) return
    setActionSlug(category.slug)
    setCardError(null)
    setSuccessMessage(null)
    try {
      const updated = await updateCategory(category.slug, { is_active: !category.is_active })
      setCategories((current) =>
        current.map((item) => (item.slug === updated.slug ? updated : item)),
      )
      setSuccessMessage(
        updated.is_active
          ? `${updated.display_name} is active again.`
          : `${updated.display_name} was disabled and will not be used for new inspections.`,
      )
    } catch (caught: unknown) {
      setCardError(errorMessage(caught, 'Category could not be updated.'))
    } finally {
      setActionSlug(null)
    }
  }

  const handleDelete = async (category: ProductCategory) => {
    if (actionSlug) return
    const confirmed = window.confirm(
      `Delete ${category.display_name}? Categories used by saved inspections must be disabled instead.`,
    )
    if (!confirmed) return
    setActionSlug(category.slug)
    setCardError(null)
    setSuccessMessage(null)
    try {
      await deleteCategory(category.slug)
      setCategories((current) => current.filter((item) => item.slug !== category.slug))
      if (editingSlug === category.slug) cancelEditing()
      setSuccessMessage(`${category.display_name} was deleted.`)
    } catch (caught: unknown) {
      setCardError(errorMessage(caught, 'Category could not be deleted.'))
    } finally {
      setActionSlug(null)
    }
  }

  return (
    <div className="categories-page">
      <section className="categories-hero" aria-label="Category overview">
        <div>
          <div className="eyebrow">
            <span className="eyebrow-dot" /> INSPECTION SETUP
          </div>
          <h1>
            Inspection
            <br />
            <span>categories.</span>
          </h1>
          <p className="hero-description">
            Choose what the inspector expects to see. Active categories are sent to the vision model
            for every new inspection, and changes apply to future photos only.
          </p>
        </div>
        <div className="category-stats" aria-label="Category counts">
          <div className="category-stat">
            <span>ACTIVE</span>
            <strong>{activeCount}</strong>
            <small>used for inspections</small>
          </div>
          <div className="category-stat">
            <span>TOTAL</span>
            <strong>{categories.length}</strong>
            <small>saved categories</small>
          </div>
        </div>
      </section>

      {loadError && (
        <div className="history-message history-error categories-load-error">
          <p>{loadError}</p>
          <button type="button" onClick={() => void loadCategories(true)}>
            Try again
          </button>
        </div>
      )}

      <div className="categories-grid">
        <section className="panel categories-list-panel" aria-label="Configured categories">
          <div className="panel-heading">
            <div>
              <p className="section-kicker">
                01 <span>/</span> CONFIGURED CATEGORIES
              </p>
              <h2>What the inspector looks for</h2>
              <p className="panel-subtitle">
                Disable a category to remove it from new inspections without losing history.
              </p>
            </div>
            <button
              className="history-refresh"
              type="button"
              onClick={() => {
                void loadCategories(true)
              }}
              disabled={isLoading}
            >
              <RefreshCw size={15} className={isLoading ? 'spin' : ''} />
              Refresh
            </button>
          </div>

          {successMessage && (
            <p className="category-success" role="status">
              <Check size={15} /> {successMessage}
            </p>
          )}
          {cardError && (
            <p className="report-error" role="alert">
              <CircleAlert size={15} /> {cardError}
            </p>
          )}

          {isLoading ? (
            <p className="history-message">Loading categories…</p>
          ) : categories.length === 0 ? (
            <div className="history-empty">
              <Sparkles size={20} />
              <p>No categories yet. Add the first one using the form.</p>
            </div>
          ) : (
            <div className="category-list">
              {categories.map((category) => {
                const isEditing = editingSlug === category.slug
                const isBusy = actionSlug === category.slug
                return (
                  <article
                    className={`category-card ${category.is_active ? '' : 'category-card-inactive'}`}
                    key={category.slug}
                  >
                    <span className="category-emoji" aria-hidden="true">
                      {category.emoji || '🌱'}
                    </span>
                    {isEditing ? (
                      <div className="category-edit">
                        <label>
                          Name
                          <input
                            value={editName}
                            maxLength={40}
                            list="fruit-suggestions"
                            onChange={(event) => setEditName(event.currentTarget.value)}
                          />
                        </label>
                        <label>
                          Emoji
                          <input
                            className="category-emoji-input"
                            value={editEmoji}
                            maxLength={MAX_EMOJI_LENGTH}
                            onChange={(event) => setEditEmoji(event.currentTarget.value)}
                          />
                        </label>
                        <div className="category-card-actions">
                          <button
                            className="category-action category-action-primary"
                            type="button"
                            onClick={() => void handleSaveEdit(category)}
                            disabled={isSavingEdit}
                          >
                            {isSavingEdit ? (
                              <LoaderCircle className="spin" size={14} />
                            ) : (
                              <Check size={14} />
                            )}
                            Save
                          </button>
                          <button
                            className="category-action"
                            type="button"
                            onClick={cancelEditing}
                            disabled={isSavingEdit}
                          >
                            <X size={14} /> Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <div className="category-copy">
                          <div className="category-title-row">
                            <strong>{category.display_name}</strong>
                            <span
                              className={`category-badge ${
                                category.is_active
                                  ? 'category-badge-active'
                                  : 'category-badge-inactive'
                              }`}
                            >
                              {category.is_active ? 'Active' : 'Disabled'}
                            </span>
                          </div>
                          <span className="category-slug">{category.slug}</span>
                          {formatCreatedAt(category.created_at) && (
                            <small>Added {formatCreatedAt(category.created_at)}</small>
                          )}
                        </div>
                        <div className="category-card-actions">
                          <button
                            className="category-action"
                            type="button"
                            onClick={() => startEditing(category)}
                            disabled={isBusy || isSavingEdit}
                          >
                            <Pencil size={14} /> Rename
                          </button>
                          <button
                            className="category-action"
                            type="button"
                            onClick={() => void handleToggleActive(category)}
                            disabled={isBusy || isSavingEdit}
                          >
                            {isBusy ? (
                              <LoaderCircle className="spin" size={14} />
                            ) : (
                              <Power size={14} />
                            )}
                            {category.is_active ? 'Disable' : 'Enable'}
                          </button>
                          <button
                            className="category-action category-action-danger"
                            type="button"
                            onClick={() => void handleDelete(category)}
                            disabled={isBusy || isSavingEdit}
                          >
                            <Trash2 size={14} /> Delete
                          </button>
                        </div>
                      </>
                    )}
                  </article>
                )
              })}
            </div>
          )}
        </section>

        <section className="panel category-form-panel" aria-label="Add a category">
          <div className="panel-heading">
            <div>
              <p className="section-kicker">
                02 <span>/</span> ADD A CATEGORY
              </p>
              <h2>Teach the inspector a new product</h2>
              <p className="panel-subtitle">
                Add a product name and an optional emoji. New categories are used by the next
                inspection.
              </p>
            </div>
            <span className="heading-icon">
              <Plus size={19} />
            </span>
          </div>

          <datalist id="fruit-suggestions">
            {SUGGESTED_FRUITS.map((fruit) => (
              <option value={fruit} key={fruit} />
            ))}
          </datalist>

          <form className="category-form" onSubmit={handleAdd}>
            <label>
              Product name
              <input
                value={name}
                maxLength={40}
                list="fruit-suggestions"
                placeholder="Mango"
                onChange={(event) => {
                  setName(event.currentTarget.value)
                  setFormError(null)
                  setSuccessMessage(null)
                }}
              />
              <span className="field-hint">Only edible fruit names are allowed.</span>
            </label>
            <label>
              Emoji <span className="field-optional">optional</span>
              <input
                className="category-emoji-input"
                value={emoji}
                maxLength={MAX_EMOJI_LENGTH}
                placeholder="🥭"
                onChange={(event) => setEmoji(event.currentTarget.value)}
              />
            </label>

            <div className="category-preview">
              <span>Preview</span>
              <strong>
                <i aria-hidden="true">{emoji.trim() || '🌱'}</i>
                {name.trim() || 'New category'}
              </strong>
            </div>

            {formError && (
              <p className="report-error" role="alert">
                <CircleAlert size={15} /> {formError}
              </p>
            )}

            <button className="inspect-button" type="submit" disabled={isSubmitting}>
              {isSubmitting ? (
                <>
                  <LoaderCircle className="spin" size={17} /> Adding category
                </>
              ) : (
                <>
                  <Plus size={17} /> Add category
                </>
              )}
            </button>
            <p className="button-caption">
              <Sparkles size={13} /> Names are sent to the vision model as inspection categories.
            </p>
          </form>

          <div className="category-note">
            <strong>How categories work</strong>
            <ul>
              <li>Only edible fruit names can be added as categories.</li>
              <li>Active categories are included in the vision prompt.</li>
              <li>Other edible fruit is still graded, but marked as out of category.</li>
              <li>Non-edible items stay ungraded as unsupported items.</li>
              <li>Existing inspections and PDFs keep their original category.</li>
            </ul>
          </div>
        </section>
      </div>
    </div>
  )
}

export default CategoriesPage
