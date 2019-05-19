from PIL import Image
import io

def resize_image(thumbnailPhoto):
    im = Image.open(io.BytesIO(thumbnailPhoto))
    width, height = im.size
    _min = min(im.size)
    _max = max(im.size)
    if _min == _max:
        # nothing to resize
        return thumbnailPhoto, width, height
    else:
        if height == _max:
            left = 0
            top = (height - _min)/2
            right = width
            bottom = (height + _min)/2
            avatar = im.crop((left, top, right, bottom))
        else:
            left = (width - _min)/2
            top = 0
            right = (width + _min)/2
            bottom = height
            avatar = im.crop((left, top, right, bottom))
        b = io.BytesIO()
        width, height = avatar.size
        avatar.save(b, 'PNG')
        return b.getvalue(), width, height
