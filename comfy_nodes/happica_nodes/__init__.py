"""Happica's own ComfyUI nodes.

PhotoMakerEncodeMulti: PhotoMaker with 1-4 photos of the same person (ComfyUI's PhotoMakerEncode only uses the
first). As in the original PhotoMaker, the class word is repeated once per photo right before the trigger word
("woman woman woman photomaker ..."), and each repeat carries one photo's face; the faces are then blended.
"""
import torch

import comfy.clip_vision


class PhotoMakerEncodeMulti:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"photomaker": ("PHOTOMAKER",), "image": ("IMAGE",), "clip": ("CLIP",),
                             "text": ("STRING", {"multiline": True, "default": "woman photomaker"})}}

    RETURN_TYPES = ("CONDITIONING",)
    FUNCTION = "encode"
    CATEGORY = "happica"

    def encode(self, photomaker, image, clip, text):
        n = image.shape[0]  # one photo per image in the batch
        pixel_values = comfy.clip_vision.clip_preprocess(image.to(photomaker.load_device)).float()
        words = text.split(" ")
        if "photomaker" not in words:
            cond, pooled = clip.encode_from_tokens(clip.tokenize(text), return_pooled=True)
            return ([[cond, {"pooled_output": pooled}]],)
        index = words.index("photomaker") + 1  # the trigger's word id (word ids start at 1)
        tokens = clip.tokenize(text, return_word_ids=True)
        out_tokens = {}
        for k in tokens:  # drop the trigger word itself, as PhotoMakerEncode does
            out_tokens[k] = []
            for t in tokens[k]:
                f = [x for x in t if x[2] != index]
                while len(f) < len(t):
                    f.append(t[-1])
                out_tokens[k].append(f)
        cond, pooled = clip.encode_from_tokens(out_tokens, return_pooled=True)
        # the n class words right before the trigger (one-token words: word k sits at token position k)
        mask = [index - n <= i <= index - 1 for i in range(77)]
        out = photomaker(id_pixel_values=pixel_values.unsqueeze(0), prompt_embeds=cond.to(photomaker.load_device),
                         class_tokens_mask=torch.tensor(mask, dtype=torch.bool, device=photomaker.load_device).unsqueeze(0))
        return ([[out, {"pooled_output": pooled}]],)


NODE_CLASS_MAPPINGS = {"PhotoMakerEncodeMulti": PhotoMakerEncodeMulti}
NODE_DISPLAY_NAME_MAPPINGS = {"PhotoMakerEncodeMulti": "PhotoMaker Encode (several photos)"}
