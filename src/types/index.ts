export type Condition = 'new_with_tags' | 'excellent' | 'good' | 'fair';

export type ListingStatus = 'draft' | 'active' | 'sold' | 'archived';

export interface Brand {
  id: string;
  name: string;
  tier: 'designer' | 'premium' | 'contemporary';
}

export interface Category {
  id: string;
  name: string;
  parent_id: string | null;
}

export interface TrendRanking {
  brand_id: string;
  brand_name: string;
  category_id: string;
  category_name: string;
  score: number; // 0-100 composite trend score
  driver: 'search_spike' | 'sold_velocity' | 'social_buzz';
  search_interest: number;
  sold_velocity: number;
  mention_count: number;
  history: { date: string; score: number }[];
}

export interface PriceSuggestion {
  low: number;
  median: number;
  high: number;
  compsUsed: number;
}

export interface Listing {
  id: string;
  brand_id: string | null;
  category_id: string | null;
  title: string;
  description: string | null;
  condition: Condition;
  price: number;
  suggested_price_low: number | null;
  suggested_price_high: number | null;
  status: ListingStatus;
  photos: string[];
}
