"""
AestheticScorer — CLIP ViT-B/32 backbone with 7 regression heads.
Fine-tuned on the PARA dataset (rsinema/aesthetic-scorer on HuggingFace).

Outputs 7 raw scores in the 0-5 range:
  aesthetic, quality, composition, light, color, dof, content
"""

import torch.nn as nn


class AestheticScorer(nn.Module):
    """
    Fine-tuned CLIP model to predict aesthetic scores based on the PARA dataset.
    backbone: transformers CLIPVisionModel (ViT-B/32)
    """

    SCORE_KEYS = ["aesthetic", "quality", "composition", "light", "color", "dof", "content"]

    def __init__(self, backbone):
        super().__init__()
        self.backbone = backbone
        hidden_dim = backbone.config.hidden_size  # 768 for ViT-B/32
        # nn.Sequential wrapping is intentional — matches the saved state dict
        # keys: head.0.weight / head.0.bias  (not head.weight / head.bias)
        self.aesthetic_head   = nn.Sequential(nn.Linear(hidden_dim, 1))
        self.quality_head     = nn.Sequential(nn.Linear(hidden_dim, 1))
        self.composition_head = nn.Sequential(nn.Linear(hidden_dim, 1))
        self.light_head       = nn.Sequential(nn.Linear(hidden_dim, 1))
        self.color_head       = nn.Sequential(nn.Linear(hidden_dim, 1))
        self.dof_head         = nn.Sequential(nn.Linear(hidden_dim, 1))
        self.content_head     = nn.Sequential(nn.Linear(hidden_dim, 1))

    def forward(self, pixel_values):
        feat = self.backbone(pixel_values).pooler_output
        return (
            self.aesthetic_head(feat),
            self.quality_head(feat),
            self.composition_head(feat),
            self.light_head(feat),
            self.color_head(feat),
            self.dof_head(feat),
            self.content_head(feat),
        )
