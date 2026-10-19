"""JPEG/PNG decoding and metadata-free display copy, without remote images."""
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from audio_storage import StorageError


def prepare_cover(session, stream, filename, limits):
    if not isinstance(filename,str) or not filename.lower().endswith(('.jpg','.jpeg','.png')):
        raise StorageError('invalid_cover','Une cover JPEG ou PNG est requise.')
    original=session.receive(stream,filename,limits.cover_bytes)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(original.path,formats=('JPEG','PNG')) as image:
                fmt=image.format
                width,height=image.size
                if (getattr(image,'is_animated',False) or width<1 or height<1 or width>limits.cover_dimension
                        or height>limits.cover_dimension or width*height>limits.cover_pixels):
                    raise StorageError('invalid_cover','Dimensions ou animation de la cover non autorisées.')
                if (fmt=='PNG') != filename.lower().endswith('.png'):
                    raise StorageError('invalid_cover','Le format réel de la cover ne correspond pas à son extension.')
                image.verify()
            with Image.open(original.path,formats=('JPEG','PNG')) as image:
                image.load()  # full pixel decode, not just header recognition
                display=ImageOps.exif_transpose(image).convert('RGBA' if fmt=='PNG' else 'RGB')
                display.info.clear()
                output,path=session.new_file()
                with output:
                    display.save(output,format=fmt)
                    output.flush()
                    import os
                    os.fsync(output.fileno())
                mime='image/png' if fmt=='PNG' else 'image/jpeg'
                normalized=session.describe(path,'display.png' if fmt=='PNG' else 'display.jpg',
                    mime=mime,width=display.width,height=display.height)
                display.close()
                if normalized.size>limits.cover_bytes:
                    raise StorageError('file_too_large','La cover normalisée dépasse la taille autorisée.')
        original=session.describe(original.path,filename,mime=mime,width=width,height=height)
        return original,normalized
    except StorageError: raise
    except (UnidentifiedImageError,OSError,ValueError,SyntaxError,Image.DecompressionBombError,Image.DecompressionBombWarning) as error:
        raise StorageError('invalid_cover','Le contenu de la cover est invalide ou corrompu.') from error
