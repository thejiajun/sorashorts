-- Migration 003: Create storage buckets and policies

-- Create private buckets
INSERT INTO storage.buckets (id, name, public) VALUES ('user-photos', 'user-photos', false);
INSERT INTO storage.buckets (id, name, public) VALUES ('project-assets', 'project-assets', false);

-- user-photos bucket policies
CREATE POLICY "sorashorts_user_photos_insert"
  ON storage.objects FOR INSERT
  WITH CHECK (
    bucket_id = 'user-photos'
    AND (storage.foldername(name))[1] = auth.uid()::text
  );

CREATE POLICY "sorashorts_user_photos_select"
  ON storage.objects FOR SELECT
  USING (
    bucket_id = 'user-photos'
    AND (storage.foldername(name))[1] = auth.uid()::text
  );

CREATE POLICY "sorashorts_user_photos_delete"
  ON storage.objects FOR DELETE
  USING (
    bucket_id = 'user-photos'
    AND (storage.foldername(name))[1] = auth.uid()::text
  );

-- project-assets bucket policies
CREATE POLICY "sorashorts_project_assets_insert"
  ON storage.objects FOR INSERT
  WITH CHECK (
    bucket_id = 'project-assets'
    AND (storage.foldername(name))[1] = auth.uid()::text
  );

CREATE POLICY "sorashorts_project_assets_select"
  ON storage.objects FOR SELECT
  USING (
    bucket_id = 'project-assets'
    AND (storage.foldername(name))[1] = auth.uid()::text
  );

CREATE POLICY "sorashorts_project_assets_delete"
  ON storage.objects FOR DELETE
  USING (
    bucket_id = 'project-assets'
    AND (storage.foldername(name))[1] = auth.uid()::text
  );
