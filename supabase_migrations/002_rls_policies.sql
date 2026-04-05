-- Migration 002: Enable RLS and create policies for all tables

-- Enable RLS
ALTER TABLE user_photos ENABLE ROW LEVEL SECURITY;
ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_assets ENABLE ROW LEVEL SECURITY;

-- user_photos policies
CREATE POLICY "Users can select own photos"
  ON user_photos FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own photos"
  ON user_photos FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own photos"
  ON user_photos FOR UPDATE
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can delete own photos"
  ON user_photos FOR DELETE
  USING (auth.uid() = user_id);

-- projects policies
CREATE POLICY "Users can select own projects"
  ON projects FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own projects"
  ON projects FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own projects"
  ON projects FOR UPDATE
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can delete own projects"
  ON projects FOR DELETE
  USING (auth.uid() = user_id);

-- project_assets policies (join through projects to check ownership)
CREATE POLICY "Users can select own project assets"
  ON project_assets FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM projects
      WHERE projects.id = project_assets.project_id
      AND projects.user_id = auth.uid()
    )
  );

CREATE POLICY "Users can insert own project assets"
  ON project_assets FOR INSERT
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM projects
      WHERE projects.id = project_assets.project_id
      AND projects.user_id = auth.uid()
    )
  );

CREATE POLICY "Users can update own project assets"
  ON project_assets FOR UPDATE
  USING (
    EXISTS (
      SELECT 1 FROM projects
      WHERE projects.id = project_assets.project_id
      AND projects.user_id = auth.uid()
    )
  )
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM projects
      WHERE projects.id = project_assets.project_id
      AND projects.user_id = auth.uid()
    )
  );

CREATE POLICY "Users can delete own project assets"
  ON project_assets FOR DELETE
  USING (
    EXISTS (
      SELECT 1 FROM projects
      WHERE projects.id = project_assets.project_id
      AND projects.user_id = auth.uid()
    )
  );
