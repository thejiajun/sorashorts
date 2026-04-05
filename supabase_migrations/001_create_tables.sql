-- Migration 001: Create core tables for SoraShorts
-- Tables: user_photos, projects, project_assets

-- 1. user_photos - stores user uploaded photos
CREATE TABLE user_photos (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  storage_path TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_user_photos_user_id ON user_photos(user_id);

-- 2. projects - stores user projects (show + storyboard)
CREATE TABLE projects (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  show_name TEXT NOT NULL,
  user_name TEXT NOT NULL,
  gender TEXT NOT NULL DEFAULT 'male',
  storyboard_json JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_projects_user_id ON projects(user_id);

-- 3. project_assets - stores generated images/videos per act
CREATE TABLE project_assets (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  act_number INTEGER NOT NULL,
  asset_type TEXT NOT NULL CHECK (asset_type IN ('image', 'video')),
  storage_path TEXT NOT NULL,
  original_url TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_project_assets_project_id ON project_assets(project_id);
