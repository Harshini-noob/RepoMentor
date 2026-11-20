import React from 'react';
import { ClaimTag } from '../types';

interface TagBadgeProps {
  tag: ClaimTag | string;
}

export const TagBadge: React.FC<TagBadgeProps> = ({ tag }) => {
  const normalized = (tag || '').toLowerCase();
  let label = 'Uncertain';
  let className = 'tag-badge uncertain';

  if (normalized === 'fact') {
    label = 'Fact';
    className = 'tag-badge fact';
  } else if (normalized === 'inferred') {
    label = 'Inferred';
    className = 'tag-badge inferred';
  }

  return <span className={className}>{label}</span>;
};
