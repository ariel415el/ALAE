from utils.costume_layers import *

STARTING_DIM = 4
STARTING_CHANNELS = 512


class MappingFromLatent(nn.Module):
    def __init__(self, num_layers=5, input_dim=256, out_dim=256):
        super(MappingFromLatent, self).__init__()
        layers = [LREQ_FC_Layer(input_dim, out_dim, lrmul=0.1), nn.LeakyReLU(0.2)]
        for i in range(num_layers - 1):
            layers += [LREQ_FC_Layer(out_dim, out_dim, lrmul=0.1), nn.LeakyReLU(0.2)]
        self.mapping = torch.nn.Sequential(*layers)

    def forward(self, x):
        x = pixel_norm(x)

        x = self.mapping(x)

        return x


class DiscriminatorFC(nn.Module):
    def __init__(self, num_layers, input_dim=256):
        super(DiscriminatorFC, self).__init__()
        assert num_layers >= 2
        layers = []
        for i in range(num_layers):
            out_dim = 1 if i == num_layers - 1 else input_dim
            layers += [LREQ_FC_Layer(input_dim, out_dim, lrmul=0.1), nn.LeakyReLU(0.2)]
        self.mapping = torch.nn.Sequential(*layers)

    def forward(self, x):
        x = self.mapping(x)
        x = x.view(-1)
        return x


class EncoderFC(nn.Module):
    def __init__(self, input_img_dim, latent_dim):
        super(EncoderFC, self).__init__()
        self.out_dim = latent_dim
        self.input_img_dim = input_img_dim

        self.fc_1 = LREQ_FC_Layer(input_img_dim ** 2, 1024)
        self.fc_2 = LREQ_FC_Layer(1024, 1024)
        self.fc_3 = LREQ_FC_Layer(1024, latent_dim)

    def encode(self, x):
        x = x.view(x.shape[0], self.input_img_dim**2)

        x = self.fc_1(x)
        x = F.leaky_relu(x, 0.2)
        x = self.fc_2(x)
        x = F.leaky_relu(x, 0.2)
        x = self.fc_3(x)
        x = F.leaky_relu(x, 0.2)

        return x

    def forward(self, x):
        return self.encode(x)


class GeneratorFC(nn.Module):
    def __init__(self, latent_dim, output_img_dim):
        super(GeneratorFC, self).__init__()
        self.latent_size = latent_dim
        self.output_img_dim = output_img_dim

        self.fc_1 = LREQ_FC_Layer(latent_dim, 1024)
        self.fc_2 = LREQ_FC_Layer(1024, 1024)
        self.fc_3 = LREQ_FC_Layer(1024, self.output_img_dim ** 2)

    def forward(self, x):
        x = self.fc_1(x)
        x = F.leaky_relu(x, 0.2)
        x = self.fc_2(x)
        x = F.leaky_relu(x, 0.2)
        x = self.fc_3(x)

        x = x.view(x.shape[0], 1, self.output_img_dim, self.output_img_dim)

        return x


class StyleGeneratorBlock(nn.Module):
    def __init__(self, latent_dim, in_channels, out_channels, is_first_block=False, upscale=False):
        super(StyleGeneratorBlock, self).__init__()
        assert not (is_first_block and upscale), "You should not upscale if this is the first block in the generator"
        self.is_first_block = is_first_block
        self.upscale = upscale
        if is_first_block:
            self.const_input = nn.Parameter(torch.randn(1, out_channels, STARTING_DIM, STARTING_DIM))
        else:
            self.blur = LearnablePreScaleBlur(out_channels)
            self.conv1 = Lreq_Conv2d(in_channels, out_channels, 3, padding=1)

        self.style_affine_transform_1 = StyleAffineTransform(latent_dim, out_channels)
        self.style_affine_transform_2 = StyleAffineTransform(latent_dim, out_channels)
        self.noise_scaler_1 = NoiseScaler(out_channels)
        self.noise_scaler_2 = NoiseScaler(out_channels)
        self.adain = AdaIn(in_channels)
        self.lrelu = nn.LeakyReLU(0.2)
        self.conv2 = Lreq_Conv2d(out_channels, out_channels, 3, padding=1)

        self.name = f"StyleBlock({latent_dim}, {in_channels}, {out_channels}, is_first_block={is_first_block}, upscale={upscale})"

    def __str__(self):
        return self. name

    def forward(self, input, latent_w, noise):
        if self.is_first_block:
            assert(input is None)
            result = self.const_input.repeat(latent_w.shape[0], 1, 1, 1)
        else:
            if self.upscale:
                input = upscale_2d(input)
            result = self.conv1(input)
            result = self.blur(result)

        result += self.noise_scaler_1(noise)
        result = self.adain(result, self.style_affine_transform_1(latent_w))
        result = self.lrelu(result)

        result = self.conv2(result)
        result += self.noise_scaler_2(noise)
        result = self.adain(result, self.style_affine_transform_2(latent_w))
        result = self.lrelu(result)

        return result


class StylleGanGenerator(nn.Module):
    def __init__(self, latent_dim, progression):
        super(StylleGanGenerator, self).__init__()
        self.latent_dim = latent_dim
        assert progression[0][0] == STARTING_DIM, f"Resolution progression should start from {STARTING_DIM}"
        self.to_rgb = nn.ModuleList([])
        self.conv_blocks = nn.ModuleList([])
        for i in range(len(progression)):
            self.to_rgb.append(Lreq_Conv2d(progression[i][1], 3, 1, 0))
            if i == 0:
                self.conv_blocks.append(StyleGeneratorBlock(latent_dim, STARTING_CHANNELS, progression[i][1],
                                                            is_first_block=True))
            else:
                upscale = (progression[i - 1][0] * 2 == progression[i][0])
                self.conv_blocks.append(StyleGeneratorBlock(latent_dim, progression[i - 1][1], progression[i][1],
                                                            upscale=upscale))
        print("Creating Style-Generator:")
        print("\ttoRgb")
        for i in range(len(self.conv_blocks)):
            print("\t", self.to_rgb[i])
        print("\tStyleBlock")
        for i in range(len(self.conv_blocks)):
            print("\t", self.conv_blocks[i])

    def forward(self, w, final_resolution_idx, alpha):
        generated_img = None
        feature_maps = None
        for i, block in enumerate(self.conv_blocks):
            # Separate noise for each block
            noise = torch.randn((w.shape[0], 1, 1, 1), dtype=torch.float32).to(w.device)

            prev_feature_maps = feature_maps
            feature_maps = block(feature_maps, w, noise)

            if i == final_resolution_idx:
                generated_img = self.to_rgb[i](feature_maps)

                # If there is an already stabilized last previous resolution layer. alpha blend with it
                if i > 0 and alpha < 1:
                    generated_img_without_last_block = self.to_rgb[i - 1](prev_feature_maps)
                    if block.upscale:
                        generated_img_without_last_block = upscale_2d(generated_img_without_last_block)
                    generated_img = alpha * generated_img + (1 - alpha) * generated_img_without_last_block
                break

        return generated_img


class PGGanDescriminatorBlock(nn.Module):
    def __init__(self, in_channels, out_channels, downsample=True):
        super(PGGanDescriminatorBlock, self).__init__()
        self.downsample = downsample
        self.conv1 = Lreq_Conv2d(in_channels, out_channels, 3, 1)
        self.lrelu = nn.LeakyReLU(0.2)
        if downsample:
            self.conv2 = torch.nn.Sequential(LearnablePreScaleBlur(out_channels),
                                             Lreq_Conv2d(out_channels, out_channels, 3, 1),
                                             torch.nn.AvgPool2d(2, 2))
        else:
            self.conv2 = Lreq_Conv2d(out_channels, out_channels, STARTING_DIM, 0)

    def forward(self, x):
        x = self.conv1(x)
        x = self.lrelu(x)

        x = self.conv2(x)
        x = self.lrelu(x)

        return x


class PGGanDiscriminator(nn.Module):
    def __init__(self):
        # self.resolutions = [4,8,16,32,64]
        super().__init__()
        self.from_rgbs = nn.ModuleList([
            Lreq_Conv2d(3, 256, 1, 0),  # 4x4 imgs
            Lreq_Conv2d(3, 128, 1, 0),
            Lreq_Conv2d(3, 64, 1, 0),
            Lreq_Conv2d(3, 32, 1, 0),
            Lreq_Conv2d(3, 16, 1, 0) # 64x64 imgs
        ])
        self.convs = nn.ModuleList([
            PGGanDescriminatorBlock(16, 32),
            PGGanDescriminatorBlock(32, 64),
            PGGanDescriminatorBlock(64, 128),
            PGGanDescriminatorBlock(128, 256),
            PGGanDescriminatorBlock(256 + 1, 512, downsample=False)
        ])
        assert(len(self.convs) == len(self.from_rgbs))
        self.fc = LREQ_FC_Layer(512, 1)
        self.n_layers = len(self.convs)

    def forward(self, image, final_resolution_idx, alpha=1):
        feature_maps = self.from_rgbs[final_resolution_idx](image)

        first_layer_idx = self.n_layers - final_resolution_idx - 1
        for i in range(first_layer_idx, self.n_layers):
            # Before final layer, do minibatch stddev:  adds a constant std channel
            if i == self.n_layers - 1:
                res_var = feature_maps.var(0, unbiased=False) + 1e-8 # Avoid zero
                res_std = torch.sqrt(res_var)
                mean_std = res_std.mean().expand(feature_maps.size(0), 1, STARTING_DIM, STARTING_DIM)
                feature_maps = torch.cat([feature_maps, mean_std], 1)

            feature_maps = self.convs[i](feature_maps)

            # If this is the first conv block to be run and this is not the last one the there is an already stabilized
            # previous scale layers : Alpha blend the output of the unstable new layer with the downscaled putput
            # of the previous one
            if i == first_layer_idx and i != self.n_layers - 1 and alpha < 1:
                skip_first_block_feature_maps =  self.from_rgbs[final_resolution_idx - 1](downscale_2d(image))
                feature_maps = alpha * feature_maps + (1 - alpha) * skip_first_block_feature_maps

        # Convert it into [batch, channel(512)], so the fully-connetced layer
        # could process it.
        feature_maps = feature_maps.squeeze(2).squeeze(2)
        result = self.fc(feature_maps)
        return result


class AlaeEncoderBlockBlock(nn.Module):
    def __init__(self, latent_dim, in_channels, out_channels, downsample=False, is_last_block=False):
        super(AlaeEncoderBlockBlock, self).__init__()
        assert not (is_last_block and downsample), "You should not downscale after last block"
        self.downsample = downsample
        self.is_last_block = is_last_block
        self.conv1 = Lreq_Conv2d(in_channels, in_channels, 3, 1)
        self.lrelu = nn.LeakyReLU(0.2)
        self.instance_norm_1 = StyleInstanceNorm2d(in_channels)
        self.c_1 = LREQ_FC_Layer(2 * in_channels, latent_dim)
        if is_last_block:
            self.conv2 = Lreq_Conv2d(in_channels, out_channels, STARTING_DIM, 0)
            self.c_2 = LREQ_FC_Layer(out_channels, latent_dim)
        else:
            scale = 2 if downsample else 1
            self.conv2 = torch.nn.Sequential(LearnablePreScaleBlur(in_channels),
                                             Lreq_Conv2d(in_channels, out_channels, 3, 1),
                                             torch.nn.AvgPool2d(scale, scale))
            self.instance_norm_2 = StyleInstanceNorm2d(out_channels)
            self.c_2 = LREQ_FC_Layer(2 * out_channels, latent_dim)

        self.name = f"EncodeBlock({latent_dim}, {in_channels}, {out_channels}, is_last_block={is_last_block}, downsample={downsample})"

    def __str__(self):
        return self.name

    def forward(self, x):
        x = self.conv1(x)
        x = self.lrelu(x)

        x, style_1 = self.instance_norm_1(x)
        w1 = self.c_1(style_1.squeeze(3).squeeze(2))

        x = self.conv2(x)
        x = self.lrelu(x)
        if self.is_last_block:
            w2 = self.c_2(x.squeeze(3).squeeze(2))
        else:
            x, style_2 = self.instance_norm_2(x)
            w2 = self.c_2(style_2.squeeze(3).squeeze(2))

        return x, w1, w2


class AlaeEncoder(nn.Module):
    def __init__(self, latent_dim, progression):
        super().__init__()
        assert progression[0][0] == STARTING_DIM, f"Resolution progression should start from {STARTING_DIM}"
        self.latent_size = latent_dim
        self.from_rgbs = nn.ModuleList([])
        self.conv_blocks = nn.ModuleList([])
        for i in range(len(progression)):
            self.from_rgbs.append(Lreq_Conv2d(3, progression[i][1], 1, 0))
        for i in range(len(progression) - 1, -1, -1):
            if i == 0:
                self.conv_blocks.append(AlaeEncoderBlockBlock(latent_dim, progression[i][1], progression[i - 1][1],
                                                              is_last_block=True))
            else:
                downsample = progression[i][0] / 2 == progression[i - 1][0]
                self.conv_blocks.append(AlaeEncoderBlockBlock(latent_dim, progression[i][1], progression[i - 1][1],
                                                            downsample=downsample))


        assert(len(self.conv_blocks) == len(self.from_rgbs))
        self.n_layers = len(self.conv_blocks)

        print("Creating ALAE-Encoder:")
        print("\tfromRgb")
        for i in range(len(self.conv_blocks)):
            print("\t", self.from_rgbs[i])
        print("\tEncodeBlock")
        for i in range(len(self.conv_blocks)):
            print("\t", self.conv_blocks[i])


    def forward(self, image, final_resolution_idx, alpha=1):
        latent_vector = torch.zeros(image.shape[0], self.latent_size).to(image.device)

        feature_maps = self.from_rgbs[final_resolution_idx](image)

        first_layer_idx = self.n_layers - final_resolution_idx - 1
        for i in range(first_layer_idx, self.n_layers):
            feature_maps, w1, w2 = self.conv_blocks[i](feature_maps)
            latent_vector += w1 + w2

            # If this is the first conv block to be run and this is not the last one the there is an already stabilized
            # previous scale layers : Alpha blend the output of the unstable new layer with the downscaled putput
            # of the previous one
            if i == first_layer_idx and i != self.n_layers - 1 and alpha < 1:
                if self.conv_blocks[i].downsample:
                    image = downscale_2d(image)
                skip_first_block_feature_maps =  self.from_rgbs[final_resolution_idx - 1](image)
                feature_maps = alpha * feature_maps + (1 - alpha) * skip_first_block_feature_maps

        return latent_vector

