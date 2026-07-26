// Generates a listing title + description.
// Default: free, template-based (no API key, no cost — this is the $0-budget
// path). If ANTHROPIC_API_KEY is set, uses Claude for a more natural,
// specific write-up. Both paths return the same shape so the UI doesn't care
// which one ran.
export interface ListingContentInput {
  brandName: string;
  categoryName: string;
  condition: 'new_with_tags' | 'excellent' | 'good' | 'fair';
  notes?: string; // free-text detail from the seller, e.g. "size 8, minor scuff on heel"
}

export interface ListingContent {
  title: string;
  description: string;
  source: 'template' | 'ai';
}

const conditionPhrase: Record<ListingContentInput['condition'], string> = {
  new_with_tags: 'New With Tags',
  excellent: 'Excellent Pre-Owned Condition',
  good: 'Good Pre-Owned Condition',
  fair: 'Fair / Well-Loved Condition',
};

function generateTemplate(input: ListingContentInput): ListingContent {
  const { brandName, categoryName, condition, notes } = input;
  const title = `${brandName} ${categoryName} — ${conditionPhrase[condition]}`;

  const description = [
    `Authentic ${brandName} ${categoryName.toLowerCase()}, ${conditionPhrase[condition].toLowerCase()}.`,
    notes ? notes.trim() : null,
    'Smoke-free home. Ships securely within 2 business days. Message with any questions before buying.',
  ]
    .filter(Boolean)
    .join(' ');

  return { title, description, source: 'template' };
}

async function generateWithClaude(input: ListingContentInput): Promise<ListingContent> {
  const { default: Anthropic } = await import('@anthropic-ai/sdk');
  const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

  const prompt = `Write a resale listing title (under 80 chars) and a 2-3 sentence description for:
Brand: ${input.brandName}
Item type: ${input.categoryName}
Condition: ${conditionPhrase[input.condition]}
Seller notes: ${input.notes ?? 'none'}

Respond as JSON: {"title": "...", "description": "..."}`;

  const response = await client.messages.create({
    model: 'claude-sonnet-5',
    max_tokens: 300,
    messages: [{ role: 'user', content: prompt }],
  });

  const text = response.content.find((b) => b.type === 'text')?.text ?? '{}';
  const parsed = JSON.parse(text.match(/\{[\s\S]*\}/)?.[0] ?? '{}');

  if (!parsed.title || !parsed.description) {
    throw new Error('Claude response missing expected fields');
  }

  return { title: parsed.title, description: parsed.description, source: 'ai' };
}

export async function generateListingContent(input: ListingContentInput): Promise<ListingContent> {
  if (!process.env.ANTHROPIC_API_KEY) {
    return generateTemplate(input);
  }
  try {
    return await generateWithClaude(input);
  } catch (err) {
    console.warn('AI generation failed, falling back to template:', err);
    return generateTemplate(input);
  }
}
